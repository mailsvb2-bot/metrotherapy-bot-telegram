from __future__ import annotations

import pytest

from services.validators.base import ValidationError
from services.validators import delivery_contracts


def _run_demo_validator(monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    def _fake_read(path: str) -> str:
        assert path == "handlers/mood_flow/ratings.py"
        return source

    monkeypatch.setattr(delivery_contracts, "_read", _fake_read)
    delivery_contracts.validate_demo_idempotency_cleanup(strict=True)


def _run_auto_audio_validator(monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    def _fake_read(path: str) -> str:
        assert path == "services/auto_audio.py"
        return source

    monkeypatch.setattr(delivery_contracts, "_read", _fake_read)
    delivery_contracts.validate_auto_audio_pre_score_lifecycle(strict=True)


def test_delivery_validator_accepts_multiline_final_marker_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '''
async def _send_audio():
    mark_delivery_once(
        int(cb.from_user.id),
        idem_kind,
        "audio",
        idem_scheduled_at,
    )
    unmark_delivery(
        int(cb.from_user.id),
        idem_kind,
        "audio_lock",
        idem_scheduled_at,
    )
    await asyncio.to_thread(
        acquire_delivery_lock,
        user_id,
        idem_kind,
        "audio_lock",
        idem_scheduled_at,
        final_stage="audio",
    )
'''

    _run_demo_validator(monkeypatch, source)


def test_delivery_validator_rejects_missing_lock_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '''
async def _send_audio():
    await asyncio.to_thread(acquire_delivery_lock, user_id, idem_kind, "audio_lock", idem_scheduled_at, final_stage="audio")
    mark_delivery_once(
        int(cb.from_user.id),
        idem_kind,
        "audio",
        idem_scheduled_at,
    )
'''

    with pytest.raises(ValidationError):
        _run_demo_validator(monkeypatch, source)


def test_delivery_validator_rejects_old_final_marker_before_send_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '''
async def _send_audio():
    else:
            if not mark_delivery_once(user_id, idem_kind, "audio", idem_scheduled_at):
                return
    unmark_delivery(int(cb.from_user.id), idem_kind, "audio_lock", idem_scheduled_at)
'''

    with pytest.raises(ValidationError):
        _run_demo_validator(monkeypatch, source)


def test_auto_audio_validator_accepts_two_phase_pre_score_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '''
async def tick(bot):
    if await asyncio.to_thread(was_delivered, uid, kind, "pre_score", scheduled_at):
        return
    if not await asyncio.to_thread(mark_delivery_once, uid, kind, "pre_score_lock", scheduled_at):
        return
    try:
        await _send_pre_prompt(bot, uid, session_id=sid, channel=channel, senders=senders)
        await asyncio.to_thread(mark_delivery_once, uid, kind, "pre_score", scheduled_at)
        await _unmark_pre_score_lock(uid, kind, scheduled_at)

def _unmark_pre_score_lock(uid, kind, scheduled_at):
    return asyncio.to_thread(unmark_delivery, uid, kind, "pre_score_lock", scheduled_at)
'''

    _run_auto_audio_validator(monkeypatch, source)


def test_auto_audio_validator_accepts_reclaimable_pre_score_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '''
async def tick(bot):
    if await asyncio.to_thread(was_delivered, uid, kind, "pre_score", scheduled_at):
        return
    lock = await asyncio.to_thread(
        acquire_delivery_lock,
        uid,
        kind,
        "pre_score_lock",
        scheduled_at,
        final_stage="pre_score",
    )
    if not lock.acquired:
        return
    try:
        await _send_pre_prompt(bot, uid, session_id=sid, channel=channel, senders=senders)
        await asyncio.to_thread(mark_delivery_once, uid, kind, "pre_score", scheduled_at)
        await _unmark_pre_score_lock(uid, kind, scheduled_at)

def _unmark_pre_score_lock(uid, kind, scheduled_at):
    return asyncio.to_thread(unmark_delivery, uid, kind, "pre_score_lock", scheduled_at)
'''

    _run_auto_audio_validator(monkeypatch, source)


def test_auto_audio_validator_rejects_missing_pre_score_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '''
async def tick(bot):
    if await asyncio.to_thread(was_delivered, uid, kind, "pre_score", scheduled_at):
        return
    await asyncio.to_thread(mark_delivery_once, uid, kind, "pre_score", scheduled_at)
'''

    with pytest.raises(ValidationError):
        _run_auto_audio_validator(monkeypatch, source)
