from __future__ import annotations

from core.time_utils import utc_now_iso
from services.db import db, mark_delivery_once
from services.jobs import add_job, claim_due_jobs, lock_job


def test_lock_job_releases_delivery_marker_when_lock_is_lost():
    user_id = 424242001
    job_type = 'demo_send'
    job_key = 'lost-lock-marker-contract'
    delivery_key = f'job:{job_type}:{job_key}'

    assert add_job(user_id, job_type, utc_now_iso(), {'kind': 'work'}, job_key=job_key) is True
    claimed = claim_due_jobs(utc_now_iso(), limit=1)
    assert claimed
    job = next(j for j in claimed if j.user_id == user_id and j.job_key == job_key)

    assert mark_delivery_once(user_id, 'job', job_type, job_key) is True

    with db() as conn:
        conn.execute(
            "UPDATE jobs SET lock_token=? WHERE id=?",
            ('stolen-lock-token', int(job.id)),
        )

    assert lock_job(int(job.id), job.lock_token) is False

    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
            (user_id, delivery_key),
        ).fetchone()
    assert row is None
