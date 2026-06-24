from __future__ import annotations

from core.time_utils import utc_now_iso
from services.db import db, mark_delivery_once, was_delivered
from services.jobs import add_job, claim_due_jobs, lock_job, mark_done


def _cleanup_job_contract(user_id: int, job_key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM jobs WHERE user_id=? OR job_key=?", (int(user_id), str(job_key)))
        conn.execute("DELETE FROM idempotency WHERE user_id=?", (int(user_id),))


def _claim_contract_job(user_id: int, job_type: str, job_key: str):
    claimed = claim_due_jobs(utc_now_iso(), limit=10)
    return next(j for j in claimed if j.user_id == user_id and j.job_type == job_type and j.job_key == job_key)


def test_engine_job_marker_is_deferred_until_successful_mark_done():
    user_id = 424242001
    job_type = "demo_send"
    job_key = "deferred-marker-success-contract"
    _cleanup_job_contract(user_id, job_key)
    try:
        assert add_job(user_id, job_type, utc_now_iso(), {"kind": "work"}, job_key=job_key) is True
        job = _claim_contract_job(user_id, job_type, job_key)

        # Compatibility call from Engine.tick must no longer create a delivered marker before the effect.
        assert mark_delivery_once(user_id, "job", job_type, job_key) is True
        assert was_delivered(user_id, "job", job_type, job_key) is False

        assert lock_job(int(job.id), job.lock_token) is True
        assert mark_done(int(job.id), job.lock_token) is True
        assert was_delivered(user_id, "job", job_type, job_key) is True
    finally:
        _cleanup_job_contract(user_id, job_key)


def test_engine_job_error_completion_does_not_write_delivered_marker():
    user_id = 424242002
    job_type = "demo_send"
    job_key = "deferred-marker-error-contract"
    _cleanup_job_contract(user_id, job_key)
    try:
        assert add_job(user_id, job_type, utc_now_iso(), {"kind": "work"}, job_key=job_key) is True
        job = _claim_contract_job(user_id, job_type, job_key)

        assert lock_job(int(job.id), job.lock_token) is True
        assert mark_done(int(job.id), job.lock_token, last_error="boom") is True
        assert was_delivered(user_id, "job", job_type, job_key) is False
    finally:
        _cleanup_job_contract(user_id, job_key)
