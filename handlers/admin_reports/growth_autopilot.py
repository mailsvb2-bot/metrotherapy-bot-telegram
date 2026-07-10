from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Callable

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from core.callback_utils import safe_answer_callback
from handlers.admin_inline_common import AdminCtx, safe_edit
from services.admin_permissions import GROWTH_APPLY_REVIEW_PERMISSION

_PERIODS = {"today", "week", "month", "all"}


def _normalize_period_light(period: str | None) -> str:
    value = (period or "today").strip().lower()
    return value if value in _PERIODS else "today"


def _period_from_callback(data: str | None) -> str:
    parts = str(data or "").split(":")
    for part in reversed(parts):
        if part in _PERIODS:
            return _normalize_period_light(part)
    return "today"


def _card_id_from_callback(data: str | None) -> str | None:
    raw = str(data or "")
    prefix = "admin:growth:autopilot:action:"
    if not raw.startswith(prefix):
        return None
    tail = raw[len(prefix):]
    for suffix in (":today", ":week", ":month", ":all"):
        if tail.endswith(suffix):
            return tail[: -len(suffix)]
    return tail or None


def _callback_parts(data: str) -> list[str]:
    return [part for part in str(data or "").split(":") if part]


def _request_id_from_callback(data: str) -> int:
    parts = _callback_parts(data)
    for marker in ("req", "prep"):
        if marker not in parts:
            continue
        index = parts.index(marker)
        offset = 2 if marker == "prep" else 1
        try:
            return int(parts[index + offset])
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("growth_apply_request_id_missing") from exc
    raise ValueError("growth_apply_request_id_missing")


def _decision_from_callback(data: str) -> str:
    parts = _callback_parts(data)
    if "prep" not in parts:
        raise ValueError("growth_apply_decision_missing")
    index = parts.index("prep") + 1
    if index >= len(parts):
        raise ValueError("growth_apply_decision_missing")
    decision = parts[index]
    if decision not in {"approve", "reject"}:
        raise ValueError("invalid_review_decision")
    return decision


def _token_from_callback(data: str, marker: str) -> str:
    parts = _callback_parts(data)
    if marker not in parts:
        raise ValueError("review_confirmation_token_missing")
    index = parts.index(marker) + 1
    if index >= len(parts) or not parts[index]:
        raise ValueError("review_confirmation_token_missing")
    return parts[index]


def _can_render_review_controls(ctx: AdminCtx) -> bool:
    if ctx.is_superadmin:
        return True
    return (
        ctx.allowed_perms is not None
        and GROWTH_APPLY_REVIEW_PERMISSION in ctx.allowed_perms
    )


def _report_builder() -> Callable[[str], str]:
    from services.growth_autopilot import build_growth_autopilot_report

    return build_growth_autopilot_report


def _inbox_builder() -> Callable[[str], str]:
    from services.growth_autopilot import build_growth_action_inbox_report

    return build_growth_action_inbox_report


def _card_builder() -> Callable[[str, str | None], str]:
    from services.growth_autopilot import build_growth_action_card_report

    return build_growth_action_card_report


def _conversion_builder() -> Callable[[str], str]:
    from services.growth_conversion_runtime_report import build_growth_conversion_runtime_report

    return build_growth_conversion_runtime_report


def _apply_gateway_builder() -> Callable[[], str]:
    from services.growth_apply_gateway import build_apply_gateway_report

    return build_apply_gateway_report


def _apply_snapshot_builder() -> Callable[..., dict[str, Any]]:
    from services.growth_apply_gateway import apply_gateway_snapshot

    return apply_gateway_snapshot


def _review_preview_builder() -> Callable[..., dict[str, Any]]:
    from services.growth_apply_review import review_request_preview

    return review_request_preview


def _review_prepare_builder() -> Callable[..., dict[str, Any]]:
    from services.growth_apply_review import prepare_review_confirmation

    return prepare_review_confirmation


def _review_consume_builder() -> Callable[..., dict[str, Any]]:
    from services.growth_apply_review import consume_review_confirmation

    return consume_review_confirmation


def _review_cancel_builder() -> Callable[..., bool]:
    from services.growth_apply_review import cancel_review_confirmation

    return cancel_review_confirmation


def _period_buttons(active: str, *, target: str) -> list[list[InlineKeyboardButton]]:
    labels = (
        ("today", "Сегодня"),
        ("week", "7 дней"),
        ("month", "30 дней"),
        ("all", "Всё время"),
    )
    rows: list[list[InlineKeyboardButton]] = [[], []]
    for index, (key, title) in enumerate(labels):
        prefix = "✅ " if key == active else ""
        rows[0 if index < 2 else 1].append(
            InlineKeyboardButton(
                text=f"{prefix}{title}",
                callback_data=f"admin:growth:autopilot:{target}:{key}",
            )
        )
    return rows


def _growth_nav(active: str) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text="📥 Action Inbox", callback_data=f"admin:growth:autopilot:inbox:{active}")],
        [InlineKeyboardButton(text="🧪 Conversion Hub", callback_data=f"admin:growth:autopilot:conversions:{active}")],
        [InlineKeyboardButton(text="🛡 Guarded Apply", callback_data=f"admin:growth:autopilot:apply:{active}")],
        [InlineKeyboardButton(text="🤖 Отчёт Growth Autopilot", callback_data=f"admin:growth:autopilot:report:{active}")],
    ]


def _home_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")]


def _kb(active: str) -> InlineKeyboardMarkup:
    rows = _period_buttons(active, target="report")
    rows.extend(_growth_nav(active)[:3])
    rows.append([InlineKeyboardButton(text="📣 Рекламные ссылки", callback_data="admin:adlinks")])
    rows.append([InlineKeyboardButton(text="💰 Деньги и клиенты", callback_data="admin:money:today")])
    rows.append(_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _inbox_kb(active: str) -> InlineKeyboardMarkup:
    rows = _period_buttons(active, target="inbox")
    rows.append([InlineKeyboardButton(text="🔎 Открыть первую карточку", callback_data=f"admin:growth:autopilot:action:ga:1:{active}")])
    rows.extend(_growth_nav(active)[1:])
    rows.append(_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _card_kb(active: str) -> InlineKeyboardMarkup:
    rows = _growth_nav(active)
    rows.append(_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _conversion_kb(active: str) -> InlineKeyboardMarkup:
    rows = _period_buttons(active, target="conversions")
    rows.extend(_growth_nav(active)[0:1] + _growth_nav(active)[2:])
    rows.append(_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _apply_kb(
    active: str,
    snapshot: dict[str, Any] | None = None,
) -> InlineKeyboardMarkup:
    """Build Guarded Apply navigation.

    ``snapshot`` remains optional for backward compatibility with the original
    read-only surface contract and its regression tests.
    """

    rows: list[list[InlineKeyboardButton]] = []
    for item in list((snapshot or {}).get("latest") or [])[:8]:
        request_id = int(item.get("id") or 0)
        status = str(item.get("status") or "unknown")
        action_type = str(item.get("action_type") or "action")
        rows.append([
            InlineKeyboardButton(
                text=f"🔎 #{request_id} {action_type} · {status}",
                callback_data=f"admin:growth:autopilot:apply:req:{request_id}:{active}",
            )
        ])
    rows.extend(_growth_nav(active)[:2] + _growth_nav(active)[3:])
    rows.append(_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _request_text(preview: dict[str, Any]) -> str:
    request = dict(preview.get("request") or {})
    evaluation = dict(preview.get("evaluation") or {})
    violations = list(evaluation.get("violations") or [])
    return "\n".join([
        f"🛡 Guarded Apply · заявка #{request.get('id')}",
        "",
        f"Статус: {request.get('status')}",
        f"Действие: {request.get('action_type')}",
        f"Платформа: {request.get('target_platform')}",
        f"Цель: {request.get('target_ref')}",
        f"Запросил: {request.get('requested_by')}",
        f"Создана: {request.get('requested_at')}",
        f"Истекает: {request.get('expires_at') or '—'}",
        "",
        f"Policy passed: {bool(evaluation.get('policy_passed'))}",
        f"Нарушения: {', '.join(str(item) for item in violations) if violations else 'нет'}",
        "",
        "dispatch_allowed=False",
        "Одобрение означает только review. Исполняющего adapter нет.",
    ])


def _request_kb(
    active: str,
    preview: dict[str, Any],
    *,
    can_review: bool,
) -> InlineKeyboardMarkup:
    request_id = int(dict(preview.get("request") or {}).get("id") or 0)
    rows: list[list[InlineKeyboardButton]] = []
    if can_review and bool(preview.get("can_approve")):
        rows.append([
            InlineKeyboardButton(
                text="✅ Подготовить одобрение",
                callback_data=f"admin:growth:autopilot:apply:prep:approve:{request_id}:{active}",
            )
        ])
    if can_review and bool(preview.get("can_reject")):
        rows.append([
            InlineKeyboardButton(
                text="⛔ Подготовить отклонение",
                callback_data=f"admin:growth:autopilot:apply:prep:reject:{request_id}:{active}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ К Guarded Apply", callback_data=f"admin:growth:autopilot:apply:{active}")])
    rows.append(_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirmation_text(prepared: dict[str, Any]) -> str:
    request = dict(prepared.get("request") or {})
    decision = str(prepared.get("decision") or "")
    label = "ОДОБРИТЬ" if decision == "approve" else "ОТКЛОНИТЬ"
    return "\n".join([
        "⚠️ Финальное подтверждение",
        "",
        f"Решение: {label}",
        f"Заявка: #{request.get('id')} {request.get('action_type')}",
        f"Платформа: {request.get('target_platform')}",
        f"Цель: {request.get('target_ref')}",
        f"Токен истекает: {prepared.get('expires_at')}",
        "",
        "После подтверждения изменится только review-статус.",
        "dispatch_allowed останется False.",
    ])


def _confirmation_kb(active: str, prepared: dict[str, Any]) -> InlineKeyboardMarkup:
    token = str(prepared.get("token") or "")
    decision = str(prepared.get("decision") or "")
    label = "✅ Да, одобрить" if decision == "approve" else "⛔ Да, отклонить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"admin:growth:autopilot:apply:confirm:{token}:{active}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"admin:growth:autopilot:apply:cancel:{token}:{active}")],
        _home_row(),
    ])


def _degraded_apply_text(reason: BaseException) -> str:
    return "\n".join([
        "🛡 Guarded Apply Gateway",
        "Статус: DEGRADED",
        f"Причина: {type(reason).__name__}",
        "dispatch_allowed=False",
    ])


async def _show_apply_overview(cb: CallbackQuery, period: str) -> None:
    snapshot: dict[str, Any] = {}
    try:
        text = await asyncio.to_thread(_apply_gateway_builder())
    except RuntimeError as exc:
        text = _degraded_apply_text(exc)
    except OSError as exc:
        text = _degraded_apply_text(exc)
    except sqlite3.Error as exc:
        text = _degraded_apply_text(exc)

    try:
        snapshot = await asyncio.to_thread(_apply_snapshot_builder())
    except RuntimeError:
        snapshot = {}
    except OSError:
        snapshot = {}
    except sqlite3.Error:
        snapshot = {}

    await safe_edit(cb, text, reply_markup=_apply_kb(period, snapshot))


async def _alert_failure(cb: CallbackQuery, exc: BaseException) -> None:
    await safe_answer_callback(cb, str(exc), show_alert=True)


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    del state, log
    data = str(getattr(cb, "data", "") or "")
    period = _period_from_callback(data)

    if data.startswith("admin:growth:autopilot:inbox"):
        text = await asyncio.to_thread(_inbox_builder(), period)
        await safe_edit(cb, text, reply_markup=_inbox_kb(period))
        return True

    if data.startswith("admin:growth:autopilot:action:"):
        text = await asyncio.to_thread(_card_builder(), period, _card_id_from_callback(data))
        await safe_edit(cb, text, reply_markup=_card_kb(period))
        return True

    if data.startswith("admin:growth:autopilot:conversions"):
        text = await asyncio.to_thread(_conversion_builder(), period)
        await safe_edit(cb, text, reply_markup=_conversion_kb(period))
        return True

    if data.startswith("admin:growth:autopilot:apply:req:"):
        try:
            preview = await asyncio.to_thread(
                _review_preview_builder(),
                request_id=_request_id_from_callback(data),
                admin_id=ctx.uid,
            )
        except PermissionError:
            await safe_answer_callback(cb, "Нет права review Growth Apply.", show_alert=True)
            return True
        except ValueError as exc:
            await _alert_failure(cb, exc)
            return True
        except RuntimeError as exc:
            await _alert_failure(cb, exc)
            return True
        await safe_edit(
            cb,
            _request_text(preview),
            reply_markup=_request_kb(
                period,
                preview,
                can_review=_can_render_review_controls(ctx),
            ),
        )
        return True

    if data.startswith("admin:growth:autopilot:apply:prep:"):
        try:
            prepared = await asyncio.to_thread(
                _review_prepare_builder(),
                request_id=_request_id_from_callback(data),
                decision=_decision_from_callback(data),
                admin_id=ctx.uid,
            )
        except PermissionError:
            await safe_answer_callback(cb, "Нет права review Growth Apply.", show_alert=True)
            return True
        except ValueError as exc:
            await _alert_failure(cb, exc)
            return True
        except RuntimeError as exc:
            await _alert_failure(cb, exc)
            return True
        await safe_edit(
            cb,
            _confirmation_text(prepared),
            reply_markup=_confirmation_kb(period, prepared),
        )
        return True

    if data.startswith("admin:growth:autopilot:apply:confirm:"):
        try:
            result = await asyncio.to_thread(
                _review_consume_builder(),
                token=_token_from_callback(data, "confirm"),
                admin_id=ctx.uid,
            )
        except PermissionError:
            await safe_answer_callback(cb, "Нет права review Growth Apply.", show_alert=True)
            return True
        except ValueError as exc:
            await _alert_failure(cb, exc)
            return True
        except RuntimeError as exc:
            await _alert_failure(cb, exc)
            return True
        request = dict(result.get("request") or {})
        await safe_answer_callback(
            cb,
            f"Заявка #{request.get('id')}: {request.get('status')}",
            show_alert=True,
        )
        await _show_apply_overview(cb, period)
        return True

    if data.startswith("admin:growth:autopilot:apply:cancel:"):
        try:
            await asyncio.to_thread(
                _review_cancel_builder(),
                token=_token_from_callback(data, "cancel"),
                admin_id=ctx.uid,
            )
        except PermissionError:
            await safe_answer_callback(cb, "Нет права review Growth Apply.", show_alert=True)
            return True
        except ValueError as exc:
            await _alert_failure(cb, exc)
            return True
        except RuntimeError as exc:
            await _alert_failure(cb, exc)
            return True
        await _show_apply_overview(cb, period)
        return True

    if data.startswith("admin:growth:autopilot:apply"):
        await _show_apply_overview(cb, period)
        return True

    text = await asyncio.to_thread(_report_builder(), period)
    await safe_edit(cb, text, reply_markup=_kb(period))
    return True
