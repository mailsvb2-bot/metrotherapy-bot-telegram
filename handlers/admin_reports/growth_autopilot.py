from __future__ import annotations

import asyncio
from typing import Callable

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.admin_inline_common import AdminCtx, safe_edit


def _normalize_period_light(period: str | None) -> str:
    value = (period or "today").strip().lower()
    return value if value in {"today", "week", "month", "all"} else "today"


def _period_from_callback(data: str | None) -> str:
    raw = str(data or "")
    if raw.startswith("admin:growth:autopilot:") or raw.startswith("admin:growth:actions:"):
        return _normalize_period_light(raw.rsplit(":", 1)[-1])
    return "today"


def _report_builder() -> Callable[[str], str]:
    # Keep the DB-heavy analytics service out of admin-router import time.
    # Smoke/startup validation should not touch optional growth tables unless an
    # admin explicitly opens this report.
    from services.growth_autopilot import build_growth_autopilot_report

    return build_growth_autopilot_report


def _action_inbox_builder() -> Callable[[str], str]:
    # Same lazy boundary as the main report: Action Inbox is read-only but still
    # depends on the Growth snapshot adapter, so do not import it at router load.
    from services.growth_autopilot import build_growth_action_inbox_report

    return build_growth_action_inbox_report


def _kb(active: str, *, view: str = "report") -> InlineKeyboardMarkup:
    labels = [
        ("today", "Сегодня"),
        ("week", "7 дней"),
        ("month", "30 дней"),
        ("all", "Всё время"),
    ]
    rows = []
    first = []
    second = []
    base = "admin:growth:actions" if view == "actions" else "admin:growth:autopilot"
    for idx, (key, title) in enumerate(labels):
        prefix = "✅ " if key == active else ""
        btn = InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"{base}:{key}")
        (first if idx < 2 else second).append(btn)
    rows.append(first)
    rows.append(second)
    if view == "actions":
        rows.append([InlineKeyboardButton(text="🤖 Отчёт автопилота", callback_data=f"admin:growth:autopilot:{active}")])
    else:
        rows.append([InlineKeyboardButton(text="📌 Action Inbox", callback_data=f"admin:growth:actions:{active}")])
    rows.append([InlineKeyboardButton(text="📣 Рекламные ссылки", callback_data="admin:adlinks")])
    rows.append([InlineKeyboardButton(text="💰 Деньги и клиенты", callback_data="admin:money:today")])
    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    del state, ctx, log
    data = str(getattr(cb, "data", "") or "")
    period = _period_from_callback(data)
    if data.startswith("admin:growth:actions"):
        build_report = _action_inbox_builder()
        text = await asyncio.to_thread(build_report, period)
        await safe_edit(cb, text, reply_markup=_kb(period, view="actions"))
        return True

    build_report = _report_builder()
    text = await asyncio.to_thread(build_report, period)
    await safe_edit(cb, text, reply_markup=_kb(period))
    return True
