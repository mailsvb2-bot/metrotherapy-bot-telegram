from __future__ import annotations

from services.db import db
from scripts.probe_auto_audio_dry_run import AutoAudioProbeResult, PROBE_SOURCE, run_probe


def test_auto_audio_dry_run_probe_verifies_and_cleans_local_path() -> None:
    user_id = -910_000_301
    result = run_probe(user_id=user_id, slot="morning", keep_artifacts=False)

    assert isinstance(result, AutoAudioProbeResult)
    assert result.user_id == user_id
    assert result.slot == "morning"
    assert isinstance(result.session_id, int)
    assert result.session_id > 0
    assert result.cleanup_status == "clean"
    assert result.rows_touched > 0

    with db() as conn:
        session_row = conn.execute(
            "SELECT 1 FROM mood_sessions WHERE user_id=? AND source=? LIMIT 1",
            (user_id, PROBE_SOURCE),
        ).fetchone()
        subscription_row = conn.execute(
            "SELECT 1 FROM subscriptions WHERE user_id=? LIMIT 1",
            (user_id,),
        ).fetchone()

    assert session_row is None
    assert subscription_row is None


def test_auto_audio_dry_run_probe_can_keep_artifacts_for_manual_inspection() -> None:
    user_id = -910_000_302
    result = run_probe(user_id=user_id, slot="evening", keep_artifacts=True)

    try:
        assert isinstance(result, AutoAudioProbeResult)
        assert result.user_id == user_id
        assert result.slot == "evening"
        assert result.cleanup_status == "kept"
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
        assert str(session_row[1]) == PROBE_SOURCE
        assert str(session_row[2]) == "evening"
        assert int(session_row[3]) > 0
        assert subscription_row is not None
    finally:
        with db() as conn:
            conn.execute("DELETE FROM mood_sessions WHERE user_id=? AND source=?", (user_id, PROBE_SOURCE))
            conn.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM idempotency WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
