from __future__ import annotations

"""Live-safe probe for the DB-backed scheduler job pipeline.

The probe never sends messages. Because it writes synthetic rows to the active
storage, callers must explicitly authorize live DB mutation. Production deploy
passes that authorization deliberately; an accidental standalone invocation
fails before schema initialization or probe-ledger writes.
"""

import argparse
import json
import sqlite3
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.time_utils import utc_now_iso
from services.db import db, mark_delivery_once, was_delivered
from services.jobs import add_job, claim_due_jobs, mark_done
from services.probe_ledger import assert_synthetic_user_id, finish_probe_run, start_probe_run
from services.probe_safety import (
    ProbeInvariantError,
    ProbeMutationAuthorizationRequired,
    new_synthetic_user_id,
    require_live_db_mutation,
    safe_probe_error_code,
)
from services.schema import init_db

PROBE_JOB_TYPE = "probe_scheduler_job_live"


@dataclass(frozen=True)
class ProbeResult:
    job_id: int
    user_id: int
    job_key: str
    run_id: str
    cleanup_status: str
    residual_rows: int
    rows_touched: int


def _row_value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, TypeError, IndexError):
            pass
    try:
        return row[index]
    except (TypeError, KeyError, IndexError):
        return None


def _delete_with_count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    cur = conn.execute(sql, params)
    return max(int(getattr(cur, "rowcount", 0) or 0), 0)


def _cleanup_probe_rows(*, user_id: int, run_id: str, job_key: str) -> int:
    """Delete only rows owned by one reserved synthetic probe identity."""

    assert_synthetic_user_id(int(user_id))
    touched = 0
    with db() as conn:
        touched += _delete_with_count(
            conn,
            "DELETE FROM jobs WHERE user_id=? AND job_type=? AND job_key=?",
            (int(user_id), PROBE_JOB_TYPE, str(job_key)),
        )
        touched += _delete_with_count(
            conn,
            "DELETE FROM events WHERE user_id=? AND event_type=?",
            (int(user_id), PROBE_JOB_TYPE),
        )
        touched += _delete_with_count(
            conn,
            "DELETE FROM idempotency WHERE user_id=? AND (key=? OR key=?)",
            (int(user_id), f"job:{PROBE_JOB_TYPE}:{job_key}", str(run_id)),
        )
    return touched


def _residual_rows(*, user_id: int, run_id: str, job_key: str) -> int:
    assert_synthetic_user_id(int(user_id))
    with db() as conn:
        queries = (
            (
                "SELECT COUNT(*) FROM jobs WHERE user_id=? AND job_type=? AND job_key=?",
                (int(user_id), PROBE_JOB_TYPE, str(job_key)),
            ),
            (
                "SELECT COUNT(*) FROM events WHERE user_id=? AND event_type=?",
                (int(user_id), PROBE_JOB_TYPE),
            ),
            (
                "SELECT COUNT(*) FROM idempotency WHERE user_id=? AND (key=? OR key=?)",
                (int(user_id), f"job:{PROBE_JOB_TYPE}:{job_key}", str(run_id)),
            ),
        )
        total = 0
        for sql, params in queries:
            row = conn.execute(sql, params).fetchone()
            total += int(row[0]) if row else 0
        return total


def _safe_finish_failure(
    *,
    run_id: str,
    rows_touched: int,
    cleanup_status: str,
    error: BaseException,
    job_key: str,
) -> None:
    try:
        finish_probe_run(
            run_id=run_id,
            status="failed",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            error=safe_probe_error_code(error),
            evidence={"job_key": job_key, "error_code": safe_probe_error_code(error)},
        )
    except sqlite3.Error:
        return
    except (RuntimeError, ValueError):
        return


def _cleanup_after_failure(
    *,
    user_id: int,
    run_id: str,
    job_key: str,
    rows_touched: int,
    keep_artifacts: bool,
) -> tuple[int, str]:
    if keep_artifacts:
        return rows_touched, "kept_failed"
    try:
        touched = rows_touched + _cleanup_probe_rows(user_id=user_id, run_id=run_id, job_key=job_key)
        residual = _residual_rows(user_id=user_id, run_id=run_id, job_key=job_key)
        return touched, "clean" if residual == 0 else "residual"
    except sqlite3.Error:
        return rows_touched, "cleanup_failed"
    except (RuntimeError, ValueError):
        return rows_touched, "cleanup_failed"


def run_probe(
    *,
    user_id: int,
    keep_artifacts: bool = False,
    allow_live_db_mutation: bool,
) -> ProbeResult:
    require_live_db_mutation(allow_live_db_mutation)
    assert_synthetic_user_id(int(user_id))
    init_db()

    run_id = uuid.uuid4().hex
    run_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    job_key = f"probe:{PROBE_JOB_TYPE}:{run_id}"
    payload = {"probe": True, "run_id": run_id, "created_at": utc_now_iso()}
    start_probe_run(
        probe_type=PROBE_JOB_TYPE,
        user_id=int(user_id),
        run_id=run_id,
        evidence={"job_key": job_key, "mutation_authorized": True},
    )
    rows_touched = 0

    try:
        rows_touched += _cleanup_probe_rows(user_id=user_id, run_id=run_id, job_key=job_key)

        inserted = add_job(int(user_id), PROBE_JOB_TYPE, run_at, payload, job_key=job_key)
        if not inserted:
            raise ProbeInvariantError("enqueue_returned_duplicate")
        rows_touched += 1

        claimed = claim_due_jobs(utc_now_iso(), limit=10, lock_ttl_sec=1)
        target = next((job for job in claimed if job.job_key == job_key), None)
        if target is None:
            raise ProbeInvariantError("probe_job_not_claimed")

        idempotency_ok = mark_delivery_once(int(user_id), "job", PROBE_JOB_TYPE, job_key)
        if not idempotency_ok:
            raise ProbeInvariantError("compatibility_idempotency_returned_false")
        if was_delivered(int(user_id), "job", PROBE_JOB_TYPE, job_key):
            raise ProbeInvariantError("pre_effect_marker_written")

        if not mark_done(int(target.id), str(target.lock_token)):
            raise ProbeInvariantError("mark_done_returned_false")
        rows_touched += 1

        if not was_delivered(int(user_id), "job", PROBE_JOB_TYPE, job_key):
            raise ProbeInvariantError("final_idempotency_row_missing")

        with db() as conn:
            row = conn.execute(
                "SELECT done_at, lock_token, last_error FROM jobs WHERE id=? AND job_key=?",
                (int(target.id), job_key),
            ).fetchone()

        if row is None:
            raise ProbeInvariantError("probe_job_disappeared_before_verification")
        if not _row_value(row, "done_at", 0):
            raise ProbeInvariantError("done_at_missing")
        if _row_value(row, "lock_token", 1):
            raise ProbeInvariantError("lock_token_not_cleared")
        if _row_value(row, "last_error", 2):
            raise ProbeInvariantError("unexpected_last_error")

        cleanup_status = "kept"
        residual_rows = _residual_rows(user_id=user_id, run_id=run_id, job_key=job_key)
        if not keep_artifacts:
            rows_touched += _cleanup_probe_rows(user_id=user_id, run_id=run_id, job_key=job_key)
            residual_rows = _residual_rows(user_id=user_id, run_id=run_id, job_key=job_key)
            cleanup_status = "clean" if residual_rows == 0 else "residual"
            if residual_rows:
                raise ProbeInvariantError("cleanup_residual_rows")

        finish_probe_run(
            run_id=run_id,
            status="ok",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            evidence={
                "job_id": int(target.id),
                "job_key": job_key,
                "residual_rows": residual_rows,
            },
        )
        return ProbeResult(
            job_id=int(target.id),
            user_id=int(user_id),
            job_key=job_key,
            run_id=run_id,
            cleanup_status=cleanup_status,
            residual_rows=residual_rows,
            rows_touched=rows_touched,
        )
    except sqlite3.Error as exc:
        touched, cleanup_status = _cleanup_after_failure(
            user_id=user_id,
            run_id=run_id,
            job_key=job_key,
            rows_touched=rows_touched,
            keep_artifacts=keep_artifacts,
        )
        _safe_finish_failure(
            run_id=run_id,
            rows_touched=touched,
            cleanup_status=cleanup_status,
            error=exc,
            job_key=job_key,
        )
        raise
    except (ProbeInvariantError, RuntimeError, ValueError) as exc:
        touched, cleanup_status = _cleanup_after_failure(
            user_id=user_id,
            run_id=run_id,
            job_key=job_key,
            rows_touched=rows_touched,
            keep_artifacts=keep_artifacts,
        )
        _safe_finish_failure(
            run_id=run_id,
            rows_touched=touched,
            cleanup_status=cleanup_status,
            error=exc,
            job_key=job_key,
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the live DB-backed scheduler job/idempotency path")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--keep-artifacts", action="store_true", help="Leave synthetic probe rows for manual inspection")
    parser.add_argument("--allow-live-db-mutation", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    user_id = int(args.user_id) if args.user_id is not None else new_synthetic_user_id()
    try:
        result = run_probe(
            user_id=user_id,
            keep_artifacts=bool(args.keep_artifacts),
            allow_live_db_mutation=bool(args.allow_live_db_mutation),
        )
    except ProbeMutationAuthorizationRequired as exc:
        payload = {
            "ok": False,
            "applied": False,
            "database_touched": False,
            "error_code": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 2

    if args.json:
        print(json.dumps({"ok": True, "applied": True, **asdict(result)}, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "SCHEDULER_JOB_PROBE_OK "
            f"job_id={result.job_id} user_id={result.user_id} job_key={result.job_key} "
            f"run_id={result.run_id} cleanup={result.cleanup_status} residual={result.residual_rows} "
            f"rows_touched={result.rows_touched}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
