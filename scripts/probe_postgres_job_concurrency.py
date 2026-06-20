from __future__ import annotations

"""Live-safe Postgres scheduler concurrency probe.

The probe creates synthetic due jobs, claims only those jobs from several
concurrent workers, and verifies that no synthetic job is claimed twice. It does
not send Telegram messages and does not claim user jobs.
"""

import argparse
import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.time_utils import utc_now_iso
from services.db import db
from services.db.runtime import CONFIG
from services.jobs import add_job
from services.probe_ledger import assert_synthetic_user_id, finish_probe_run, start_probe_run
from services.schema import init_db

PROBE_TYPE = "postgres_job_concurrency_probe"
DEFAULT_USER_ID = -910_000_301


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    probe: str
    run_id: str
    workers: int
    jobs_created: int
    claimed_count: int
    duplicate_count: int
    missing_count: int
    cleanup_status: str
    rows_touched: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "probe": self.probe,
            "run_id": self.run_id,
            "workers": self.workers,
            "jobs_created": self.jobs_created,
            "claimed_count": self.claimed_count,
            "duplicate_count": self.duplicate_count,
            "missing_count": self.missing_count,
            "cleanup_status": self.cleanup_status,
            "rows_touched": self.rows_touched,
        }


def _cleanup(*, user_id: int, key_prefix: str) -> int:
    touched = 0
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM jobs WHERE user_id=? AND job_type=? AND job_key LIKE ?",
            (int(user_id), PROBE_TYPE, f"{key_prefix}%"),
        )
        touched += max(int(getattr(cur, "rowcount", 0) or 0), 0)
        cur = conn.execute(
            "DELETE FROM idempotency WHERE user_id=? AND key LIKE ?",
            (int(user_id), f"job:{PROBE_TYPE}:{key_prefix}%"),
        )
        touched += max(int(getattr(cur, "rowcount", 0) or 0), 0)
    return touched


def _claim_synthetic_jobs(*, now_iso: str, limit: int, token: str, user_id: int, key_prefix: str) -> list[int]:
    with db() as conn:
        rows = conn.execute(
            """
            WITH due AS (
                SELECT id
                FROM jobs
                WHERE user_id=?
                  AND job_type=?
                  AND job_key LIKE ?
                  AND done_at IS NULL
                  AND run_at_utc <= ?
                  AND (locked_at IS NULL OR locked_at <= ?)
                ORDER BY id ASC
                LIMIT ?
                FOR UPDATE SKIP LOCKED
            )
            UPDATE jobs
            SET locked_at=?, lock_token=?
            FROM due
            WHERE jobs.id = due.id
            RETURNING jobs.id
            """.strip(),
            (int(user_id), PROBE_TYPE, f"{key_prefix}%", now_iso, now_iso, int(limit), now_iso, token),
        ).fetchall()
    return [int(row["id"] if hasattr(row, "keys") else row[0]) for row in rows]


def _claim_worker(*, barrier: threading.Barrier, now_iso: str, limit: int, user_id: int, key_prefix: str) -> list[int]:
    barrier.wait(timeout=10)
    return _claim_synthetic_jobs(
        now_iso=now_iso,
        limit=int(limit),
        token=uuid.uuid4().hex,
        user_id=int(user_id),
        key_prefix=str(key_prefix),
    )


def run_probe(*, user_id: int = DEFAULT_USER_ID, workers: int = 4, jobs: int = 24, keep_artifacts: bool = False) -> ProbeResult:
    if not CONFIG.uses_postgres:
        raise SystemExit("POSTGRES_JOB_CONCURRENCY_PROBE_FAILED active engine is not Postgres")
    if workers < 2:
        raise SystemExit("POSTGRES_JOB_CONCURRENCY_PROBE_FAILED workers must be >= 2")
    if jobs < workers:
        raise SystemExit("POSTGRES_JOB_CONCURRENCY_PROBE_FAILED jobs must be >= workers")

    assert_synthetic_user_id(int(user_id))
    init_db()

    run_id = uuid.uuid4().hex
    key_prefix = f"probe:{PROBE_TYPE}:{run_id}:"
    run_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    start_probe_run(
        probe_type=PROBE_TYPE,
        user_id=int(user_id),
        run_id=run_id,
        evidence={"workers": int(workers), "jobs": int(jobs), "key_prefix": key_prefix},
    )
    rows_touched = 0
    try:
        rows_touched += _cleanup(user_id=int(user_id), key_prefix=key_prefix)
        for idx in range(int(jobs)):
            inserted = add_job(
                int(user_id),
                PROBE_TYPE,
                run_at,
                {"probe": True, "run_id": run_id, "idx": idx, "created_at": utc_now_iso()},
                job_key=f"{key_prefix}{idx:04d}",
            )
            if not inserted:
                raise SystemExit(f"POSTGRES_JOB_CONCURRENCY_PROBE_FAILED duplicate insert idx={idx}")
            rows_touched += 1

        barrier = threading.Barrier(int(workers))
        per_worker_limit = max(1, int(jobs) // int(workers) + 1)
        claimed: list[int] = []
        with ThreadPoolExecutor(max_workers=int(workers)) as pool:
            futures = [
                pool.submit(
                    _claim_worker,
                    barrier=barrier,
                    now_iso=utc_now_iso(),
                    limit=per_worker_limit,
                    user_id=int(user_id),
                    key_prefix=key_prefix,
                )
                for _ in range(int(workers))
            ]
            for future in as_completed(futures, timeout=30):
                claimed.extend(future.result())

        unique = set(claimed)
        duplicate_count = len(claimed) - len(unique)
        missing_count = int(jobs) - len(unique)
        if duplicate_count or missing_count:
            raise SystemExit(
                "POSTGRES_JOB_CONCURRENCY_PROBE_FAILED "
                f"claimed={len(claimed)} unique={len(unique)} duplicates={duplicate_count} missing={missing_count}"
            )

        cleanup_status = "kept"
        if not keep_artifacts:
            rows_touched += _cleanup(user_id=int(user_id), key_prefix=key_prefix)
            cleanup_status = "clean"

        result = ProbeResult(
            ok=True,
            probe=PROBE_TYPE,
            run_id=run_id,
            workers=int(workers),
            jobs_created=int(jobs),
            claimed_count=len(claimed),
            duplicate_count=duplicate_count,
            missing_count=missing_count,
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
        )
        finish_probe_run(
            run_id=run_id,
            status="ok",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            evidence=result.to_dict(),
        )
        return result
    except SystemExit as exc:
        finish_probe_run(
            run_id=run_id,
            status="failed",
            cleanup_status="kept" if keep_artifacts else "unknown",
            rows_touched=rows_touched,
            error=str(exc),
            evidence={"key_prefix": key_prefix},
        )
        raise
    except (RuntimeError, TimeoutError, ValueError, OSError, threading.BrokenBarrierError) as exc:
        finish_probe_run(
            run_id=run_id,
            status="failed",
            cleanup_status="kept" if keep_artifacts else "unknown",
            rows_touched=rows_touched,
            error=str(exc),
            evidence={"key_prefix": key_prefix},
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe native Postgres concurrent scheduler job claiming")
    parser.add_argument("--user-id", type=int, default=int(os.getenv("POSTGRES_CONCURRENCY_PROBE_USER_ID", DEFAULT_USER_ID)))
    parser.add_argument("--workers", type=int, default=int(os.getenv("POSTGRES_CONCURRENCY_PROBE_WORKERS", "4")))
    parser.add_argument("--jobs", type=int, default=int(os.getenv("POSTGRES_CONCURRENCY_PROBE_JOBS", "24")))
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    result = run_probe(user_id=int(args.user_id), workers=int(args.workers), jobs=int(args.jobs), keep_artifacts=bool(args.keep_artifacts))
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
