from __future__ import annotations

from services.db import db
from scripts.probe_scheduler_job_live import PROBE_JOB_TYPE, run_probe


def test_scheduler_job_probe_exercises_jobs_idempotency_path() -> None:
    user_id = -910_000_101
    result = run_probe(user_id=user_id, keep_artifacts=False)

    assert result.user_id == user_id
    assert result.job_key.startswith(f"probe:{PROBE_JOB_TYPE}:")

    with db() as conn:
        job_row = conn.execute(
            "SELECT 1 FROM jobs WHERE user_id=? AND job_key=? LIMIT 1",
            (user_id, result.job_key),
        ).fetchone()
        idem_row = conn.execute(
            "SELECT 1 FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
            (user_id, f"job:{PROBE_JOB_TYPE}:{result.job_key}"),
        ).fetchone()

    assert job_row is None
    assert idem_row is None


def test_scheduler_job_probe_can_keep_artifacts_for_manual_inspection() -> None:
    user_id = -910_000_102
    result = run_probe(user_id=user_id, keep_artifacts=True)

    try:
        with db() as conn:
            job_row = conn.execute(
                "SELECT done_at, lock_token, last_error FROM jobs WHERE user_id=? AND job_key=? LIMIT 1",
                (user_id, result.job_key),
            ).fetchone()
            idem_row = conn.execute(
                "SELECT 1 FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
                (user_id, f"job:{PROBE_JOB_TYPE}:{result.job_key}"),
            ).fetchone()

        assert job_row is not None
        assert job_row[0]
        assert job_row[1] is None
        assert job_row[2] is None
        assert idem_row is not None
    finally:
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE user_id=? AND job_key=?", (user_id, result.job_key))
            conn.execute(
                "DELETE FROM idempotency WHERE user_id=? AND key=?",
                (user_id, f"job:{PROBE_JOB_TYPE}:{result.job_key}"),
            )
