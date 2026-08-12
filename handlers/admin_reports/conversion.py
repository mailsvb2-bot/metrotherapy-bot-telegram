from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from services.funnel_analytics import conversion_report, format_conversion_report


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    """Render the last 30 days of the commercial funnel as a money-first report."""

    end_utc = datetime.now(timezone.utc).replace(microsecond=0)
    start_utc = end_utc - timedelta(days=30)
    report = await asyncio.to_thread(
        conversion_report,
        start_utc.isoformat(),
        end_utc.isoformat(),
    )
    text = format_conversion_report(report, title="за последние 30 дней")
    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True
