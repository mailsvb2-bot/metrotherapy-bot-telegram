from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from keyboards.inline import kb_admin_money_card, kb_admin_money_payments
from services.admin_money_clients import (
    format_money_period,
    format_payment_client_card,
    money_period_summary,
    payment_client_card,
)

_PERIODS = {"today", "week", "month", "all"}


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    data = str(cb.data or "")

    if data.startswith("admin:money:payment:"):
        raw = data.rsplit(":", 1)[-1]
        if not raw.isdigit():
            await safe_edit(cb, "❌ Не понял номер оплаты.", reply_markup=ctx.staff_kb)
            return True
        payment_id = int(raw)
        card = await asyncio.to_thread(payment_client_card, payment_id)
        await safe_edit(cb, format_payment_client_card(card), reply_markup=kb_admin_money_card(payment_id))
        return True

    period = data.rsplit(":", 1)[-1] if data.startswith("admin:money:") else "today"
    if period not in _PERIODS:
        period = "today"
    summary = await asyncio.to_thread(money_period_summary, period, limit=20)
    ids = [int(r.get("id")) for r in (summary.get("rows") or []) if str(r.get("id") or "").isdigit()]
    await safe_edit(cb, format_money_period(summary), reply_markup=kb_admin_money_payments(ids, period))
    return True
