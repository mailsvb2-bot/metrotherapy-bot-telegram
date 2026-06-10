from __future__ import annotations

"""Live-safe probe for the DB-backed scheduler job pipeline.

The probe intentionally does not send Telegram messages and does not require the
background scheduler loop to be running. It exercises the same canonical jobs
storage helpers used by ``core.engine`` and records a probe ledger row.
"""

import argparse
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.time_utils import utc_now_iso
from services.db import db, mark_delivery_once, was_delivered
from services.jobs import add_job, claim_due_jobs, mark_done
from services.probe_ledger import assert_synthetic_user_id, finish_probe_run, start_probe_run
from services.schema import init_db

PROBE_JOB_TYPE = "probe_scheduler_job_live"
DEFAULT_PROBE_USER_ID = -910_000_001


@dataclass(frozen=True)
class ProbeResult:
    job_id: int
    user_id: int
    job_key: str
    run_id: str
    cleanup_status: str
    rows_touched: int


def _row_value(row, key: str, index: int):
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


def _cleanup_probe_rows(*, user_id: int, run_id: str, job_key: str) -> int:
    """Best-effort cleanup of only this probe's rows; returns touched rows."""
    touched = 0
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM jobs WHERE user_id=? AND job_type=? AND job_key=?",
            (int(user_id), PROBE_JOB_TYPE, str(job_key)),
        )
        touched += max(int(getattr(cur, "rowcount", 0) or 0), 0)
        cur = conn.execute(
            "DELETE FROM events WHERE user_id=? AND event_type=?",
            (int(user_id), PROBE_JOB_TYPE),
        )
        touched += max(int(getattr(cur, "rowcount", 0) or 0), 0)
        # Delete both the job-level idempotency key and the raw probe run key.
        cur = conn.execute(
            "DELETE FROM idempotency WHERE user_id=? AND (key=? OR key=?)",
            (int(user_id), f"job:{PROBE_JOB_TYPE}:{job_key}", str(run_id)),
        )
        touched += max(int(getattr(cur, "rowcount", 0) or 0), 0)
    return touched


def _record_failure(*, run_id: str, rows_touched: int, keep_artifacts: bool, error: BaseException, job_key: str) -> None:
    finish_probe_run(
        run_id=run_id,
        status="failed",
        cleanup_status="failed" if keep_artifacts else "unknown",
        rows_touched=rows_touched,
        error=str(error),
        evidence={"job_key": job_key},
    )


def run_probe(*, user_id: int = DEFAULT_PROBE_USER_ID, keep_artifacts: bool = False) -> ProbeResult:
    assert_synthetic_user_id(int(user_id))
    init_db()

    run_id = uuid.uuid4().hex
    run_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    job_key = f"probe:{PROBE_JOB_TYPE}:{run_id}"
    payload = {"probe": True, "run_id": run_id, "created_at": utc_now_iso()}
    start_probe_run(probe_type=PROBE_JOB_TYPE, user_id=int(user_id), run_id=run_id, evidence={"job_key": job_key})
    rows_touched = 0

    try:
        rows_touched += _cleanup_probe_rows(user_id=user_id, run_id=run_id, job_key=job_key)

        inserted = add_job(int(user_id), PROBE_JOB_TYPE, run_at, payload, job_key=job_key)
        if not inserted:
            raise SystemExit("SCHEDULER_JOB_PROBE_FAILED enqueue returned duplicate")
        rows_touched += 1

        claimed = claim_due_jobs(utc_now_iso(), limit=10, lock_ttl_sec=1)
        target = next((job for job in claimed if job.job_key == job_key), None)
        if target is None:
            raise SystemExit("SCHEDULER_JOB_PROBE_FAILED probe job was not claimed")

        idempotency_ok = mark_delivery_once(int(user_id), "job", PROBE_JOB_TYPE, job_key)
        if not idempotency_ok:
            raise SystemExit("SCHEDULER_JOB_PROBE_FAILED idempotency insert returned duplicate")
        rows_touched += 1

        if not was_delivered(int(user_id), "job", PROBE_JOB_TYPE, job_key):
            raise SystemExit("SCHEDULER_JOB_PROBE_FAILED idempotency row not visible")

        if not mark_done(int(target.id), str(target.lock_token)):
            raise SystemExit("SCHEDULER_JOB_PROBE_FAILED mark_done returned false")

        with db() as conn:
            row = conn.execute(
                "SELECT done_at, lock_token, last_error FROM jobs WHERE id=? AND job_key=?",
                (int(target.id), job_key),
            ).fetchone()

        if row is None:
            raise SystemExit("SCHEDULER_JOB_PROBE_FAILED probe job row disappeared before verification")
        if not _row_value(row, "done_at", 0):
            raise SystemExit("SCHEDULER_JOB_PROBE_FAILED done_at was not set")
        if _row_value(row, "lock_token", 1):
            raise SystemExit("SCHEDULER_JOB_PROBE_FAILED lock_token was not cleared")
        if _row_value(row, "last_error", 2):
            raise SystemExit("SCHEDULER_JOB_PROBE_FAILED unexpected last_error: " + str(_row_value(row, "last_error", 2)))

        cleanup_status = "kept"
        if not keep_artifacts:
            rows_touched += _cleanup_probe_rows(user_id=user_id, run_id=run_id, job_key=job_key)
            cleanup_status = "clean"

        finish_probe_run(
            run_id=run_id,
            status="ok",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            evidence={"job_id": int(target.id), "job_key": job_key},
        )
        return ProbeResult(
            job_id=int(target.id),
            user_id=int(user_id),
            job_key=job_key,
            run_id=run_id,
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
        )
    except SystemExit as exc:
        _record_failure(run_id=run_id, rows_touched=rows_touched, keep_artifacts=keep_artifacts, error=exc, job_key=job_key)
        raise
    except Exception as exc:  # validator: allow-wide-except
        _record_failure(run_id=run_id, rows_touched=rows_touched, keep_artifacts=keep_artifacts, error=exc, job_key=job_key)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the live DB-backed scheduler job/idempotency path")
    parser.add_argument("--user-id", type=int, default=int(os.getenv("SCHEDULER_PROBE_USER_ID", DEFAULT_PROBE_USER_ID)))
    parser.add_argument("--keep-artifacts", action="store_true", help="Leave synthetic probe rows for manual inspection")
    args = parser.parse_args()

    result = run_probe(user_id=int(args.user_id), keep_artifacts=bool(args.keep_artifacts))
    print(
        "SCHEDULER_JOB_PROBE_OK "
        f"job_id={result.job_id} user_id={result.user_id} job_key={result.job_key} "
        f"run_id={result.run_id} cleanup={result.cleanup_status} rows_touched={result.rows_touched}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
