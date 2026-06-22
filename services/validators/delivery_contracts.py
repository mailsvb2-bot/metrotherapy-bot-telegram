from __future__ import annotations

import ast

from core.paths import ROOT as PROJECT_ROOT
from services.validators.base import ValidationError


def _read(path: str) -> str:
    try:
        return (PROJECT_ROOT / path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _func_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _func_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except ValueError:
        return ""
    except RuntimeError:
        return ""


def _has_call(text: str, *, func_name: str, args: tuple[str, ...], keywords: dict[str, str] | None = None) -> bool:
    """Return True when source contains func_name(*args), independent of formatting.

    The production validator must enforce the delivery lifecycle contract without
    coupling startup to one exact line-wrapping style. This keeps the guardrail
    strict while allowing normal formatter-safe edits.
    """

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False

    expected_keywords = keywords or {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _func_name(node.func) != func_name:
            continue
        if len(node.args) < len(args):
            continue
        actual = tuple(_unparse(arg) for arg in node.args[: len(args)])
        if actual != args:
            continue
        actual_keywords = {kw.arg: _unparse(kw.value) for kw in node.keywords if kw.arg}
        if any(actual_keywords.get(key) != value for key, value in expected_keywords.items()):
            continue
        return True
    return False


def _has_to_thread_call(
    text: str,
    *,
    target: str,
    args: tuple[str, ...],
    keywords: dict[str, str] | None = None,
) -> bool:
    """Return True for asyncio.to_thread(target, *args, **keywords)."""

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False

    expected_keywords = keywords or {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _func_name(node.func) != "asyncio.to_thread":
            continue
        if not node.args:
            continue
        if _unparse(node.args[0]) != target:
            continue
        if len(node.args) < len(args) + 1:
            continue
        actual = tuple(_unparse(arg) for arg in node.args[1 : len(args) + 1])
        if actual != args:
            continue
        actual_keywords = {kw.arg: _unparse(kw.value) for kw in node.keywords if kw.arg}
        if any(actual_keywords.get(key) != value for key, value in expected_keywords.items()):
            continue
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
    has_lock = _has_to_thread_call(
        text,
        target="acquire_delivery_lock",
        args=("user_id", "idem_kind", "'audio_lock'", "idem_scheduled_at"),
        keywords={"final_stage": "'audio'"},
    ) or _has_call(
        text,
        func_name="mark_delivery_once",
        args=("user_id", "idem_kind", "'audio_lock'", "idem_scheduled_at"),
    )
    if old_demo_final_marker_before_send in text or not has_lock or not has_final_marker or not has_lock_cleanup:
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


def validate_auto_audio_pre_score_lifecycle(*, strict: bool = True) -> None:
    """Auto-audio prompt delivery must use lock-before-send and final marker-after-send."""
    text = _read("services/auto_audio.py")
    if not text:
        return

    has_final_check = _has_to_thread_call(
        text,
        target="was_delivered",
        args=("uid", "kind", "'pre_score'", "scheduled_at"),
    )
    has_lock = _has_to_thread_call(
        text,
        target="acquire_delivery_lock",
        args=("uid", "kind", "'pre_score_lock'", "scheduled_at"),
        keywords={"final_stage": "'pre_score'"},
    ) or _has_to_thread_call(
        text,
        target="mark_delivery_once",
        args=("uid", "kind", "'pre_score_lock'", "scheduled_at"),
    )
    has_final_marker = _has_to_thread_call(
        text,
        target="mark_delivery_once",
        args=("uid", "kind", "'pre_score'", "scheduled_at"),
    )
    has_lock_cleanup = _has_to_thread_call(
        text,
        target="unmark_delivery",
        args=("uid", "kind", "'pre_score_lock'", "scheduled_at"),
    ) or "_unmark_pre_score_lock(uid, kind, scheduled_at)" in text
    old_marker = "mark_delivery_once, uid, kind, \"pre_score\", scheduled_at):\n                log_event"
    if not has_final_check or not has_lock or not has_final_marker or not has_lock_cleanup or old_marker in text:
        msg = "auto_audio pre_score idempotency is not using the canonical two-phase lifecycle"
        if strict:
            raise ValidationError(msg)


def validate_delivery_contracts(*, strict: bool = True) -> None:
    validate_demo_idempotency_cleanup(strict=strict)
    validate_auto_audio_not_subscription_only(strict=strict)
    validate_auto_audio_pre_score_lifecycle(strict=strict)
