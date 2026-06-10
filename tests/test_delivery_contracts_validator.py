from __future__ import annotations

import pytest

from services.validators.base import ValidationError
from services.validators.delivery_contracts import _has_call


CANONICAL_FINAL_MARKER_ARGS = (
    "int(cb.from_user.id)",
    "idem_kind",
    "'audio'",
    "idem_scheduled_at",
)

CANONICAL_LOCK_CLEANUP_ARGS = (
    "int(cb.from_user.id)",
    "idem_kind",
    "'audio_lock'",
    "idem_scheduled_at",
)


def _assert_demo_contract_source(text: str) -> None:
    old_demo_final_marker_before_send = 'else:\n            if not mark_delivery_once(user_id, idem_kind, "audio", idem_scheduled_at):'
    has_final_marker = _has_call(
        text,
        func_name="mark_delivery_once",
        args=CANONICAL_FINAL_MARKER_ARGS,
    )
    has_lock_cleanup = _has_call(
        text,
        func_name="unmark_delivery",
        args=CANONICAL_LOCK_CLEANUP_ARGS,
    )
    if old_demo_final_marker_before_send in text or not has_final_marker or not has_lock_cleanup:
        raise ValidationError(
            "Demo audio idempotency is not using the canonical "
            "lock-before-send/final-marker-after-send lifecycle"
        )


def test_delivery_validator_accepts_multiline_final_marker_and_cleanup() -> None:
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

    _assert_demo_contract_source(source)


def test_delivery_validator_rejects_missing_lock_cleanup() -> None:
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
        _assert_demo_contract_source(source)


def test_delivery_validator_rejects_old_final_marker_before_send_pattern() -> None:
    source = '''
async def _send_audio():
    else:
            if not mark_delivery_once(user_id, idem_kind, "audio", idem_scheduled_at):
                return
    unmark_delivery(int(cb.from_user.id), idem_kind, "audio_lock", idem_scheduled_at)
'''

    with pytest.raises(ValidationError):
        _assert_demo_contract_source(source)
