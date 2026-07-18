from __future__ import annotations

import pytest

from scripts import probe_scheduler_job_live as probe_module
from services.db import db
from services.probe_safety import ProbeMutationAuthorizationRequired


def test_scheduler_job_probe_exercises_jobs_idempotency_path() -> None:
    user_id = -910_000_101
    result = probe_module.run_probe(
        user_id=user_id,
        keep_artifacts=False,
        allow_live_db_mutation=True,
    )

    assert result.user_id == user_id
    assert result.job_key.startswith(f"probe:{probe_module.PROBE_JOB_TYPE}:")
    assert result.cleanup_status == "clean"
    assert result.residual_rows == 0

    with db() as conn:
        job_row = conn.execute(
            "SELECT 1 FROM jobs WHERE user_id=? AND job_key=? LIMIT 1",
            (user_id, result.job_key),
        ).fetchone()
        idem_row = conn.execute(
            "SELECT 1 FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
            (user_id, f"job:{probe_module.PROBE_JOB_TYPE}:{result.job_key}"),
        ).fetchone()

    assert job_row is None
    assert idem_row is None


def test_scheduler_job_probe_refuses_mutation_before_schema_init(monkeypatch: pytest.MonkeyPatch) -> None:
    def bomb() -> None:
        raise AssertionError("schema initialization must not run")

    monkeypatch.setattr(probe_module, "init_db", bomb)

    with pytest.raises(ProbeMutationAuthorizationRequired):
        probe_module.run_probe(
            user_id=-910_000_103,
            keep_artifacts=False,
            allow_live_db_mutation=False,
        )


def test_scheduler_job_probe_can_keep_artifacts_for_manual_inspection() -> None:
    user_id = -910_000_102
    result = probe_module.run_probe(
        user_id=user_id,
        keep_artifacts=True,
        allow_live_db_mutation=True,
    )

    try:
        with db() as conn:
            job_row = conn.execute(
                "SELECT done_at, lock_token, last_error FROM jobs WHERE user_id=? AND job_key=? LIMIT 1",
                (user_id, result.job_key),
            ).fetchone()
            idem_row = conn.execute(
                "SELECT 1 FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
                (user_id, f"job:{probe_module.PROBE_JOB_TYPE}:{result.job_key}"),
            ).fetchone()

        assert result.cleanup_status == "kept"
        assert result.residual_rows > 0
        assert job_row is not None
        assert job_row[0]
        assert job_row[1] is None
        assert job_row[2] is None
        assert idem_row is not None
    finally:
        probe_module._cleanup_probe_rows(
            user_id=user_id,
            run_id=result.run_id,
            job_key=result.job_key,
        )
