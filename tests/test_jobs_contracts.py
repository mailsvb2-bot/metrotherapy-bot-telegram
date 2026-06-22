from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from core.time_utils import utc_now_iso
from services.db import db
from services.jobs import add_job, claim_due_jobs, mark_done, reschedule


def _cleanup(job_key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM jobs WHERE job_key=?", (job_key,))


def test_add_job_is_idempotent_by_job_key() -> None:
    job_key = f"test:job-idempotency:{uuid.uuid4().hex}"
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        assert add_job(-920000001, "test_job_idempotency", now, {"ok": True}, job_key=job_key) is True
        assert add_job(-920000001, "test_job_idempotency", now, {"ok": True}, job_key=job_key) is False
    finally:
        _cleanup(job_key)


def test_mark_done_and_reschedule_return_boolean_contracts() -> None:
    job_key = f"test:job-return-contract:{uuid.uuid4().hex}"
    retry_key_prefix = job_key.split(":a", 1)[0]
    try:
        now = utc_now_iso()
        assert add_job(-920000002, "test_job_return_contract", now, {"ok": True}, job_key=job_key) is True
        claimed = claim_due_jobs(now, limit=20, lock_ttl_sec=1)
        job = next(item for item in claimed if item.job_key == job_key)

        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=1)).replace(microsecond=0).isoformat()
        assert reschedule(job, retry_at, last_error="retry-for-contract-test") is True
        assert reschedule(job, retry_at, last_error="old-lock-token-must-not-match") is False

        claimed_again = claim_due_jobs((datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(), limit=20, lock_ttl_sec=1)
        retried = next(item for item in claimed_again if item.job_key.startswith(retry_key_prefix))
        assert mark_done(retried.id, retried.lock_token) is True
        assert mark_done(retried.id, retried.lock_token) is False
    finally:
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE job_key=? OR job_key LIKE ?", (job_key, f"{retry_key_prefix}:a%"))
