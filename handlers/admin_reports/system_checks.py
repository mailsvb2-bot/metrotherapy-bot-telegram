from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from services.probe_ledger import format_probe_runs_for_admin


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    text = await asyncio.to_thread(format_probe_runs_for_admin, limit=7)
    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True
