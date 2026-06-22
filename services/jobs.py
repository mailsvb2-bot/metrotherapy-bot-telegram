from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from core.time_utils import normalize_utc_iso, utc_now_iso
from services.db import db, tx
from services.db.runtime import CONFIG
from services.job_keys import default_job_key

log = logging.getLogger(__name__)


@dataclass
class ClaimedJob:
    id: int
    user_id: int
    job_type: str
    run_at_utc: str
    payload: str
    job_key: str
    retries: int
    lock_token: str


def _row_get(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        try:
            return row[key]
        except KeyError:
            pass
        except TypeError:
            pass
        except IndexError:
            pass
    return row[index]


def _claimed_jobs_from_rows(rows: list[Any], *, fallback_token: str) -> list[ClaimedJob]:
    out: list[ClaimedJob] = []
    for row in rows:
        out.append(
            ClaimedJob(
                id=int(_row_get(row, "id", 0)),
                user_id=int(_row_get(row, "user_id", 1)),
                job_type=str(_row_get(row, "job_type", 2)),
                run_at_utc=str(_row_get(row, "run_at_utc", 3)),
                payload=str(_row_get(row, "payload", 4) or "{}"),
                job_key=str(_row_get(row, "job_key", 5) or ""),
                retries=int(_row_get(row, "retries", 6) or 0),
                lock_token=str(_row_get(row, "lock_token", 7) or fallback_token),
            )
        )
    return out


def _add_job_postgres(
    *,
    user_id: int,
    job_type: str,
    run_at_utc: str,
    encoded_payload: str,
    job_key: str,
) -> bool:
    """Idempotently enqueue a Postgres job with DB-enforced job_key uniqueness.

    The schema owns the invariant through the partial unique index
    idx_jobs_job_key on jobs(job_key) where job_key is not null. The advisory
    lock is retained only as a low-cost contention guard for concurrent workers;
    correctness no longer depends on a pre-insert existence check.
    """
    with db() as conn:
        with tx(conn):
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(?)::bigint)", (str(job_key),))
            cur = conn.execute(
                """
                INSERT INTO jobs(
                    user_id, job_type, run_at_utc, payload,
                    job_key, retries, locked_at, lock_token, done_at, last_error
                ) VALUES(?,?,?,?,?, 0, NULL, NULL, NULL, NULL)
                ON CONFLICT (job_key) WHERE job_key IS NOT NULL DO NOTHING
                """.strip(),
                (int(user_id), str(job_type), run_at_utc, encoded_payload, str(job_key)),
            )
            return int(getattr(cur, "rowcount", 0) or 0) == 1


def add_job(
    user_id: int,
    job_type: str,
    run_at_utc: str,
    payload: dict | None = None,
    *,
    job_key: str | None = None,
) -> bool:
    """Enqueue a job (UTC ISO timebase).

    - run_at_utc is always normalized to tz-aware UTC ISO.
    - job_key is used for idempotent enqueue; duplicates become no-op.
    """
    run_at_utc = normalize_utc_iso(run_at_utc)
    payload_obj = payload or {}
    if job_key is None:
        job_key = default_job_key(int(user_id), str(job_type), run_at_utc, payload_obj)
    encoded_payload = json.dumps(payload_obj, ensure_ascii=False)

    if CONFIG.uses_postgres:
        return _add_job_postgres(
            user_id=int(user_id),
            job_type=str(job_type),
            run_at_utc=run_at_utc,
            encoded_payload=encoded_payload,
            job_key=str(job_key),
        )

    with db() as conn:
        with tx(conn):
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO jobs(
                    user_id, job_type, run_at_utc, payload,
                    job_key, retries, locked_at, lock_token, done_at, last_error
                ) VALUES(?,?,?,?,?, 0, NULL, NULL, NULL, NULL)
                """.strip(),
                (int(user_id), str(job_type), run_at_utc, encoded_payload, str(job_key)),
            )
            return int(getattr(cur, "rowcount", 0) or 0) == 1


def cancel_jobs(user_id: int, job_types: list[str] | None = None, prefix: str | None = None) -> None:
    user_id = int(user_id)
    if not job_types and not prefix:
        return
    with db() as conn:
        with tx(conn):
            if job_types:
                placeholders = ",".join(["?"] * len(job_types))
                conn.execute(
                    f"DELETE FROM jobs WHERE user_id=? AND done_at IS NULL AND job_type IN ({placeholders})",
                    [user_id, *job_types],
                )
            if prefix:
                conn.execute(
                    "DELETE FROM jobs WHERE user_id=? AND done_at IS NULL AND job_type LIKE ?",
                    (user_id, f"{prefix}%"),
                )


def cancel_funnel(user_id: int) -> None:
    cancel_jobs(int(user_id), prefix="funnel_")


def cancel_funnel2(user_id: int) -> None:
    cancel_jobs(int(user_id), prefix="funnel2_")


def cancel_post_prompt(user_id: int, session_id: int | str) -> None:
    user_id = int(user_id)
    sid = str(session_id).strip()
    if not sid:
        return
    pat1 = f'"session_id":"{sid}"'
    pat2 = f'"session_id": "{sid}"'
    with db() as conn:
        with tx(conn):
            conn.execute(
                "DELETE FROM jobs WHERE user_id=? AND done_at IS NULL AND job_type=? AND (payload LIKE ? OR payload LIKE ?)",
                (user_id, "post_prompt", f"%{pat1}%", f"%{pat2}%"),
            )


def _claim_due_jobs_postgres(*, now_utc_iso: str, stale_before: str, limit: int, token: str) -> list[ClaimedJob]:
    """Atomically claim due jobs using native Postgres row locks."""
    with db() as conn:
        with tx(conn):
            rows = conn.execute(
                """
                WITH due AS (
                    SELECT id
                    FROM jobs
                    WHERE done_at IS NULL
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
                  AND jobs.done_at IS NULL
                RETURNING jobs.id, jobs.user_id, jobs.job_type, jobs.run_at_utc,
                          jobs.payload, jobs.job_key, jobs.retries, jobs.lock_token
                """.strip(),
                (now_utc_iso, stale_before, int(limit), now_utc_iso, token),
            ).fetchall()
    return _claimed_jobs_from_rows(list(rows), fallback_token=token)


def _claim_due_jobs_sqlite(*, now_utc_iso: str, stale_before: str, limit: int, token: str) -> list[ClaimedJob]:
    with db() as conn:
        with tx(conn):
            rows = conn.execute(
                """
                SELECT id, user_id, job_type, run_at_utc, payload, job_key, retries
                FROM jobs
                WHERE done_at IS NULL
                  AND run_at_utc <= ?
                  AND (locked_at IS NULL OR locked_at <= ?)
                ORDER BY id ASC
                LIMIT ?
                """.strip(),
                (now_utc_iso, stale_before, int(limit)),
            ).fetchall()

            if not rows:
                return []

            ids = [int(_row_get(row, "id", 0)) for row in rows]
            placeholders = ",".join(["?"] * len(ids))

            conn.execute(
                f"""
                UPDATE jobs
                SET locked_at=?, lock_token=?
                WHERE id IN ({placeholders})
                  AND done_at IS NULL
                  AND (locked_at IS NULL OR locked_at <= ?)
                """.strip(),
                [now_utc_iso, token, *ids, stale_before],
            )

            claimed = conn.execute(
                f"""
                SELECT id, user_id, job_type, run_at_utc, payload, job_key, retries, lock_token
                FROM jobs
                WHERE lock_token=? AND id IN ({placeholders})
                ORDER BY id ASC
                """.strip(),
                [token, *ids],
            ).fetchall()

    return _claimed_jobs_from_rows(list(claimed), fallback_token=token)


def claim_due_jobs(now_utc_iso: str, *, limit: int = 50, lock_ttl_sec: int = 120) -> list[ClaimedJob]:
    """Claim due jobs with a DB lock.

    In production/Postgres this is a native atomic UPDATE ... RETURNING claim with
    FOR UPDATE SKIP LOCKED. SQLite keeps the previous local/dev implementation.
    """
    now_utc_iso = normalize_utc_iso(now_utc_iso)
    now_dt = datetime.fromisoformat(now_utc_iso)
    stale_before = (now_dt - timedelta(seconds=int(lock_ttl_sec))).replace(microsecond=0).isoformat()
    token = uuid.uuid4().hex

    if CONFIG.uses_postgres:
        return _claim_due_jobs_postgres(now_utc_iso=now_utc_iso, stale_before=stale_before, limit=int(limit), token=token)
    return _claim_due_jobs_sqlite(now_utc_iso=now_utc_iso, stale_before=stale_before, limit=int(limit), token=token)


def lock_job(job_id: int, lock_token: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE id=? AND lock_token=? AND done_at IS NULL",
            (int(job_id), str(lock_token)),
        ).fetchone()
        return bool(row)


def mark_done(job_id: int, lock_token: str, *, last_error: str | None = None) -> bool:
    with db() as conn:
        with tx(conn):
            cur = conn.execute(
                """
                UPDATE jobs
                SET done_at=?, locked_at=NULL, lock_token=NULL, last_error=COALESCE(?, last_error)
                WHERE id=? AND lock_token=? AND done_at IS NULL
                """.strip(),
                (utc_now_iso(), last_error, int(job_id), str(lock_token)),
            )
            return int(getattr(cur, "rowcount", 0) or 0) == 1


def reschedule(job: ClaimedJob, retry_at_utc: str, *, last_error: str | None = None) -> bool:
    """Reschedule a claimed job.

    - increments retries
    - updates job_key to a new attempt suffix so retries are not blocked by idempotency
    """
    retry_at_utc = normalize_utc_iso(retry_at_utc)
    base = str(job.job_key or "").split(":a", 1)[0]
    new_retries = int(job.retries) + 1
    new_key = f"{base}:a{new_retries}" if base else f"retry:{job.id}:a{new_retries}"

    with db() as conn:
        with tx(conn):
            cur = conn.execute(
                """
                UPDATE jobs
                SET run_at_utc=?, retries=?, job_key=?, locked_at=NULL, lock_token=NULL, last_error=?
                WHERE id=? AND lock_token=? AND done_at IS NULL
                """.strip(),
                (retry_at_utc, int(new_retries), str(new_key), last_error, int(job.id), str(job.lock_token)),
            )
            return int(getattr(cur, "rowcount", 0) or 0) == 1
