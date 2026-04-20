from __future__ import annotations
import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.events import funnel_counts


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    c = await asyncio.to_thread(
        funnel_counts,
        [
            "funnel_offer_variant_A",
            "funnel_offer_variant_B",
            "funnel_offer_nextday_variant_A",
            "funnel_offer_nextday_variant_B",
        ],
    )
    text = (
        "🧪 A/B офферы (уникальные пользователи)\n\n"
        "Offer (через 20 минут):\n"
        f"— A: {c.get('funnel_offer_variant_A', 0)}\n"
        f"— B: {c.get('funnel_offer_variant_B', 0)}\n\n"
        "Offer NextDay (на следующий день):\n"
        f"— A: {c.get('funnel_offer_nextday_variant_A', 0)}\n"
        f"— B: {c.get('funnel_offer_nextday_variant_B', 0)}\n\n"
        "ℹ️ Вариант выбирается детерминированно (без дублей при рестарте), "
        "а событие пишется в events для последующей аналитики."
    )
    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True
