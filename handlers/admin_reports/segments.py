from __future__ import annotations
import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.segments import segment_counts


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    d = await asyncio.to_thread(segment_counts)
    total = sum(int(v or 0) for v in d.values())
    # Sort by count desc
    items = sorted(((k, int(v or 0)) for k, v in d.items()), key=lambda x: x[1], reverse=True)
    lines = ["🧩 Сегменты пользователей", "", f"Всего (в пределах лимита выборки): {total}", ""]
    for k, v in items:
        pct = (v * 100 / total) if total else 0
        lines.append(f"— {k}: {v} ({pct:.1f}%)")
    await safe_edit(cb, "\n".join(lines), reply_markup=ctx.staff_kb)
    return True
