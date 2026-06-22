from __future__ import annotations

import time

from services.auto_audio_recovery import (
    acquire_delivery_lock,
    auto_audio_lock_summary,
    format_auto_audio_lock_report,
    list_stale_auto_audio_locks,
    release_stale_auto_audio_locks,
)
from services.db import db, mark_delivery_once, unmark_delivery, was_delivered


def _cleanup(user_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM idempotency WHERE user_id=?", (int(user_id),))


def test_stale_audio_lock_can_be_reclaimed_without_final_marker() -> None:
    user_id = -910_000_401
    kind = "work"
    scheduled_at = "test:stale-audio-lock"
    _cleanup(user_id)
    try:
        first = acquire_delivery_lock(user_id, kind, "audio_lock", scheduled_at, final_stage="audio")
        second = acquire_delivery_lock(user_id, kind, "audio_lock", scheduled_at, final_stage="audio", stale_after_seconds=9999)

        assert first.acquired is True
        assert first.reason == "created"
        assert second.acquired is False
        assert second.reason == "duplicate_fresh_lock"

        with db() as conn:
            conn.execute(
                "UPDATE idempotency SET created_at=? WHERE user_id=? AND key=?",
                (int(time.time()) - 3600, int(user_id), f"{kind}:audio_lock:{scheduled_at}"),
            )

        reclaimed = acquire_delivery_lock(user_id, kind, "audio_lock", scheduled_at, final_stage="audio", stale_after_seconds=1)

        assert reclaimed.acquired is True
        assert reclaimed.reason == "stale_reclaimed"
        assert reclaimed.stale_reclaimed is True
        assert was_delivered(user_id, kind, "audio_lock", scheduled_at) is True
    finally:
        _cleanup(user_id)


def test_final_audio_marker_blocks_stale_lock_reclaim() -> None:
    user_id = -910_000_402
    kind = "home"
    scheduled_at = "test:final-marker-blocks"
    _cleanup(user_id)
    try:
        assert mark_delivery_once(user_id, kind, "audio", scheduled_at) is True
        assert mark_delivery_once(user_id, kind, "audio_lock", scheduled_at) is True
        with db() as conn:
            conn.execute(
                "UPDATE idempotency SET created_at=? WHERE user_id=? AND key=?",
                (int(time.time()) - 3600, int(user_id), f"{kind}:audio_lock:{scheduled_at}"),
            )

        decision = acquire_delivery_lock(user_id, kind, "audio_lock", scheduled_at, final_stage="audio", stale_after_seconds=1)

        assert decision.acquired is False
        assert decision.reason == "final_marker_exists"
    finally:
        _cleanup(user_id)


def test_stale_auto_audio_lock_report_and_release() -> None:
    user_id = -910_000_403
    kind = "work"
    scheduled_at = "test:report-release"
    _cleanup(user_id)
    try:
        assert mark_delivery_once(user_id, kind, "pre_score_lock", scheduled_at) is True
        with db() as conn:
            conn.execute(
                "UPDATE idempotency SET created_at=? WHERE user_id=? AND key=?",
                (int(time.time()) - 3600, int(user_id), f"{kind}:pre_score_lock:{scheduled_at}"),
            )

        locks = list_stale_auto_audio_locks(stale_after_seconds=1, limit=20)
        matching = [item for item in locks if item.user_id == user_id and item.stage == "pre_score_lock"]
        summary = auto_audio_lock_summary(stale_after_seconds=1, limit=20)
        report = format_auto_audio_lock_report(stale_after_seconds=1, limit=20)

        assert matching
        assert summary["stale_lock_count"] >= 1
        assert "Stale locks:" in report
        assert "pre_score_lock" in report
        assert release_stale_auto_audio_locks(stale_after_seconds=1, limit=20, dry_run=True) >= 1
        assert release_stale_auto_audio_locks(stale_after_seconds=1, limit=20, dry_run=False) >= 1
        assert was_delivered(user_id, kind, "pre_score_lock", scheduled_at) is False
    finally:
        unmark_delivery(user_id, kind, "pre_score_lock", scheduled_at)
        _cleanup(user_id)
