from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from keyboards.inline import kb_admin_money_payments
from services.admin_payment_path import format_payment_path_report, payment_path_report


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    report = await asyncio.to_thread(payment_path_report, "today", limit=10)
    payment_ids = [int(row.get("payment_id")) for row in (report.get("rows") or []) if str(row.get("payment_id") or "").isdigit()]
    await safe_edit(cb, format_payment_path_report(report), reply_markup=kb_admin_money_payments(payment_ids, "today"))
    return True
