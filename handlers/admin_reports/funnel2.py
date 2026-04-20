from __future__ import annotations
import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.funnel2_analytics import format_report as funnel2_format


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    # Funnel 2.0 (scenario-based): format_report(title, time_min, time_max)
    now_utc = datetime.now(ZoneInfo("UTC")).replace(microsecond=0)
    title = f"Сформировано: {now_utc.isoformat()} UTC"
    txt = await asyncio.to_thread(funnel2_format, title, None, None)
    await safe_edit(cb, txt, reply_markup=ctx.staff_kb)
    return True
