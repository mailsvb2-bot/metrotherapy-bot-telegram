from __future__ import annotations
import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.funnel_analytics import conversion_report


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    # Сводный отчёт по конверсиям
    now_utc = datetime.now(ZoneInfo("UTC")).replace(microsecond=0)
    txt = await asyncio.to_thread(conversion_report, now_utc)
    await safe_edit(cb, txt, reply_markup=ctx.staff_kb)
    return True
