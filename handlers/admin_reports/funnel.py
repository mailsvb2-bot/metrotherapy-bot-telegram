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
            "funnel_demo_open",
            "funnel_demo_work",
            "funnel_demo_home",
            "funnel_demo_ack",
            "funnel_offer_shown",
            "funnel_offer_pay_clicked",
            "funnel_pay_success",
        ],
    )
    text = (
        "📉 Воронка (events)\n\n"
        f"— открыли демо: {c.get('funnel_demo_open', 0)}\n"
        f"— нажали work: {c.get('funnel_demo_work', 0)}\n"
        f"— нажали home: {c.get('funnel_demo_home', 0)}\n"
        f"— отметили прослушивание: {c.get('funnel_demo_ack', 0)}\n\n"
        f"— показали оффер: {c.get('funnel_offer_shown', 0)}\n"
        f"— нажали оплату: {c.get('funnel_offer_pay_clicked', 0)}\n"
        f"— успешная оплата: {c.get('funnel_pay_success', 0)}\n"
    )
    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True
