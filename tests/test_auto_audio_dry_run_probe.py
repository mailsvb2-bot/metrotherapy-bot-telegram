from __future__ import annotations

import pytest

from scripts import probe_auto_audio_dry_run as probe_module
from services.db import db
from services.probe_safety import ProbeMutationAuthorizationRequired


def test_auto_audio_probe_verifies_and_cleans_local_path() -> None:
    user_id = -910_000_301
    result = probe_module.run_probe(
        user_id=user_id,
        slot="morning",
        keep_artifacts=False,
        allow_live_db_mutation=True,
    )

    assert isinstance(result, probe_module.AutoAudioProbeResult)
    assert result.user_id == user_id
    assert result.slot == "morning"
    assert isinstance(result.session_id, int)
    assert result.session_id > 0
    assert result.cleanup_status == "clean"
    assert result.residual_rows == 0
    assert result.rows_touched > 0

    with db() as conn:
        session_row = conn.execute(
            "SELECT 1 FROM mood_sessions WHERE user_id=? AND source=? LIMIT 1",
            (user_id, probe_module.PROBE_SOURCE),
        ).fetchone()
        subscription_row = conn.execute(
            "SELECT 1 FROM subscriptions WHERE user_id=? LIMIT 1",
            (user_id,),
        ).fetchone()

    assert session_row is None
    assert subscription_row is None


def test_auto_audio_probe_refuses_mutation_before_schema_init(monkeypatch: pytest.MonkeyPatch) -> None:
    def bomb() -> None:
        raise AssertionError("schema initialization must not run")

    monkeypatch.setattr(probe_module, "init_db", bomb)

    with pytest.raises(ProbeMutationAuthorizationRequired):
        probe_module.run_probe(
            user_id=-910_000_303,
            slot="morning",
            keep_artifacts=False,
            allow_live_db_mutation=False,
        )


def test_auto_audio_probe_can_keep_artifacts_for_manual_inspection() -> None:
    user_id = -910_000_302
    result = probe_module.run_probe(
        user_id=user_id,
        slot="evening",
        keep_artifacts=True,
        allow_live_db_mutation=True,
    )

    try:
        assert isinstance(result, probe_module.AutoAudioProbeResult)
        assert result.user_id == user_id
        assert result.slot == "evening"
        assert result.cleanup_status == "kept"
        assert result.residual_rows > 0
        with db() as conn:
            session_row = conn.execute(
                "SELECT user_id, source, slot, anchor_id FROM mood_sessions WHERE id=?",
                (result.session_id,),
            ).fetchone()
            subscription_row = conn.execute(
                "SELECT 1 FROM subscriptions WHERE user_id=? LIMIT 1",
                (user_id,),
            ).fetchone()

        assert session_row is not None
        assert int(session_row[0]) == user_id
        assert str(session_row[1]) == probe_module.PROBE_SOURCE
        assert str(session_row[2]) == "evening"
        assert int(session_row[3]) > 0
        assert subscription_row is not None
    finally:
        probe_module._cleanup_probe_rows(user_id=user_id)
