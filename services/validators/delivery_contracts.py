from __future__ import annotations

import ast

from core.paths import ROOT as PROJECT_ROOT
from services.validators.base import ValidationError


def _read(path: str) -> str:
    try:
        return (PROJECT_ROOT / path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _has_call(text: str, *, func_name: str, args: tuple[str, ...]) -> bool:
    """Return True when source contains func_name(*args), independent of formatting.

    The production validator must enforce the delivery lifecycle contract without
    coupling startup to one exact line-wrapping style.  This keeps the guardrail
    strict while allowing normal formatter-safe edits.
    """

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != func_name:
            continue
        if len(node.args) < len(args):
            continue
        try:
            actual = tuple(ast.unparse(arg) for arg in node.args[: len(args)])
        except (ValueError, RuntimeError):
            continue
        if actual == args:
            return True
    return False


def validate_demo_idempotency_cleanup(*, strict: bool = True) -> None:
    """Demo audio idempotency must use a two-phase lock and final marker after send."""
    text = _read("handlers/mood_flow/ratings.py")
    if not text:
        return
    old_demo_final_marker_before_send = "else:\n            if not mark_delivery_once(user_id, idem_kind, \"audio\", idem_scheduled_at):"
    has_final_marker = _has_call(
        text,
        func_name="mark_delivery_once",
        args=("int(cb.from_user.id)", "idem_kind", "'audio'", "idem_scheduled_at"),
    )
    has_lock_cleanup = _has_call(
        text,
        func_name="unmark_delivery",
        args=("int(cb.from_user.id)", "idem_kind", "'audio_lock'", "idem_scheduled_at"),
    )
    if old_demo_final_marker_before_send in text or not has_final_marker or not has_lock_cleanup:
        msg = "Demo audio idempotency is not using the canonical lock-before-send/final-marker-after-send lifecycle"
        if strict:
            raise ValidationError(msg)


def validate_auto_audio_not_subscription_only(*, strict: bool = True) -> None:
    """New practice-token checkout must be connected to delivery entitlement."""
    text = _read("services/auto_audio.py")
    adapter = _read("services/auto_audio_entitlement.py")
    if not text:
        return
    connected = "services.auto_audio_entitlement" in text and "eligible_user_ids" in text and "has_entitlement" in text
    adapter_uses_tokens = "practice_wallets" in adapter and "has_access" in adapter and "get_wallet" in adapter
    if not connected or not adapter_uses_tokens:
        msg = "auto_audio delivery is not connected to unified subscription/practice-token entitlement"
        if strict:
            raise ValidationError(msg)


def validate_delivery_contracts(*, strict: bool = True) -> None:
    validate_demo_idempotency_cleanup(strict=strict)
    validate_auto_audio_not_subscription_only(strict=strict)
