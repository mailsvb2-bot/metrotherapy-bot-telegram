from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from services.release_control_report import format_release_control_report


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    text = await asyncio.to_thread(format_release_control_report, limit=25)
    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True
