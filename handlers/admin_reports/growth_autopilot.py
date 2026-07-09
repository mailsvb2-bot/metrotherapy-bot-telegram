from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.admin_inline_common import AdminCtx, safe_edit
from services.growth_autopilot import build_growth_autopilot_report, normalize_period


def _period_from_callback(data: str | None) -> str:
    raw = str(data or "")
    if raw.startswith("admin:growth:autopilot:"):
        return normalize_period(raw.rsplit(":", 1)[-1])
    return "today"


def _kb(active: str) -> InlineKeyboardMarkup:
    labels = [
        ("today", "Сегодня"),
        ("week", "7 дней"),
        ("month", "30 дней"),
        ("all", "Всё время"),
    ]
    rows = []
    first = []
    second = []
    for idx, (key, title) in enumerate(labels):
        prefix = "✅ " if key == active else ""
        btn = InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"admin:growth:autopilot:{key}")
        (first if idx < 2 else second).append(btn)
    rows.append(first)
    rows.append(second)
    rows.append([InlineKeyboardButton(text="📣 Рекламные ссылки", callback_data="admin:adlinks")])
    rows.append([InlineKeyboardButton(text="💰 Деньги и клиенты", callback_data="admin:money:today")])
    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    del state, ctx, log
    period = _period_from_callback(getattr(cb, "data", ""))
    text = await asyncio.to_thread(build_growth_autopilot_report, period)
    await safe_edit(cb, text, reply_markup=_kb(period))
    return True
