from __future__ import annotations

import asyncio
from typing import Callable

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.admin_inline_common import AdminCtx, safe_edit

_PERIODS = {"today", "week", "month", "all"}


def _normalize_period_light(period: str | None) -> str:
    value = (period or "today").strip().lower()
    return value if value in _PERIODS else "today"


def _period_from_callback(data: str | None) -> str:
    raw = str(data or "")
    parts = raw.split(":")
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


def _report_builder() -> Callable[[str], str]:
    # Keep the DB-heavy analytics service out of admin-router import time.
    # Smoke/startup validation should not touch optional growth tables unless an
    # admin explicitly opens this report.
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


def _period_buttons(active: str, *, target: str) -> list[list[InlineKeyboardButton]]:
    labels = [
        ("today", "Сегодня"),
        ("week", "7 дней"),
        ("month", "30 дней"),
        ("all", "Всё время"),
    ]
    first = []
    second = []
    for idx, (key, title) in enumerate(labels):
        prefix = "✅ " if key == active else ""
        btn = InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"admin:growth:autopilot:{target}:{key}")
        (first if idx < 2 else second).append(btn)
    return [first, second]


def _growth_nav(active: str) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text="📥 Action Inbox", callback_data=f"admin:growth:autopilot:inbox:{active}")],
        [InlineKeyboardButton(text="🧪 Conversion Hub", callback_data=f"admin:growth:autopilot:conversions:{active}")],
        [InlineKeyboardButton(text="🛡 Guarded Apply", callback_data=f"admin:growth:autopilot:apply:{active}")],
        [InlineKeyboardButton(text="🤖 Отчёт Growth Autopilot", callback_data=f"admin:growth:autopilot:report:{active}")],
    ]


def _kb(active: str) -> InlineKeyboardMarkup:
    rows = _period_buttons(active, target="report")
    rows.extend(_growth_nav(active)[:3])
    rows.append([InlineKeyboardButton(text="📣 Рекламные ссылки", callback_data="admin:adlinks")])
    rows.append([InlineKeyboardButton(text="💰 Деньги и клиенты", callback_data="admin:money:today")])
    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _inbox_kb(active: str) -> InlineKeyboardMarkup:
    rows = _period_buttons(active, target="inbox")
    rows.append([InlineKeyboardButton(text="🔎 Открыть первую карточку", callback_data=f"admin:growth:autopilot:action:ga:1:{active}")])
    rows.extend(_growth_nav(active)[1:])
    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _card_kb(active: str) -> InlineKeyboardMarkup:
    rows = _growth_nav(active)
    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _conversion_kb(active: str) -> InlineKeyboardMarkup:
    rows = _period_buttons(active, target="conversions")
    rows.extend(_growth_nav(active)[0:1] + _growth_nav(active)[2:])
    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _apply_kb(active: str) -> InlineKeyboardMarkup:
    rows = _growth_nav(active)[:2] + _growth_nav(active)[3:]
    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    del state, ctx, log
    data = str(getattr(cb, "data", "") or "")
    period = _period_from_callback(data)

    if data.startswith("admin:growth:autopilot:inbox"):
        build_inbox = _inbox_builder()
        text = await asyncio.to_thread(build_inbox, period)
        await safe_edit(cb, text, reply_markup=_inbox_kb(period))
        return True

    if data.startswith("admin:growth:autopilot:action:"):
        build_card = _card_builder()
        text = await asyncio.to_thread(build_card, period, _card_id_from_callback(data))
        await safe_edit(cb, text, reply_markup=_card_kb(period))
        return True

    if data.startswith("admin:growth:autopilot:conversions"):
        build_conversions = _conversion_builder()
        text = await asyncio.to_thread(build_conversions, period)
        await safe_edit(cb, text, reply_markup=_conversion_kb(period))
        return True

    if data.startswith("admin:growth:autopilot:apply"):
        build_gateway = _apply_gateway_builder()
        text = await asyncio.to_thread(build_gateway)
        await safe_edit(cb, text, reply_markup=_apply_kb(period))
        return True

    build_report = _report_builder()
    text = await asyncio.to_thread(build_report, period)
    await safe_edit(cb, text, reply_markup=_kb(period))
    return True
