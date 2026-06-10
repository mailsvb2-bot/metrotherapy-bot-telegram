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
'''

    _run_demo_validator(monkeypatch, source)


def test_delivery_validator_rejects_missing_lock_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '''
async def _send_audio():
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
