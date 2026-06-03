from __future__ import annotations

from pathlib import Path

from core.paths import ROOT as PROJECT_ROOT
from services.validators.base import ValidationError


def _read(path: str) -> str:
    try:
        return (PROJECT_ROOT / path).read_text(encoding="utf-8")
    except OSError:
        return ""


def validate_demo_idempotency_cleanup(*, strict: bool = True) -> None:
    """Demo audio idempotency must be reversible if send fails before delivery."""
    text = _read("handlers/mood_flow/ratings.py")
    if not text:
        return
    marker = "s.source != \"demo\""
    cleanup = "unmark_delivery(int(cb.from_user.id), idem_kind, \"audio_lock\", idem_scheduled_at)"
    demo_marker = "mark_delivery_once(user_id, idem_kind, \"audio\", idem_scheduled_at)"
    if demo_marker in text and cleanup in text and marker in text:
        msg = (
            "Demo audio idempotency marker can remain stuck after failed send. "
            "The failure cleanup is gated to non-demo sessions only."
        )
        if strict:
            raise ValidationError(msg)


def validate_auto_audio_not_subscription_only(*, strict: bool = True) -> None:
    """New practice-token checkout must be connected to delivery entitlement."""
    text = _read("services/auto_audio.py")
    if not text:
        return
    subscription_only = "FROM subscriptions" in text and "practice_wallets" not in text and "check_and_reserve_for_audio" not in text
    if subscription_only:
        msg = "auto_audio delivery still selects only subscriptions and is not connected to practice-token entitlement"
        if strict:
            raise ValidationError(msg)


def validate_delivery_contracts(*, strict: bool = True) -> None:
    validate_demo_idempotency_cleanup(strict=strict)
    validate_auto_audio_not_subscription_only(strict=strict)
