from __future__ import annotations
import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.state_log import fetch_last


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    rows = await asyncio.to_thread(fetch_last, ctx.uid, limit=10)
    if not rows:
        text = "🧾 Мои состояния\n\nПока нет записей в user_state_log."
    else:
        lines = ["🧾 Мои состояния (последние 10)\n"]
        for r in rows:
            meta = r.get("meta")
            if isinstance(meta, dict):
                meta_s = ", ".join(f"{k}={v}" for k, v in list(meta.items())[:6])
            elif meta is None:
                meta_s = ""
            else:
                meta_s = str(meta)
            meta_s = (meta_s[:140] + "…") if len(meta_s) > 140 else meta_s
            tail = f" | {meta_s}" if meta_s else ""
            lines.append(f"{r['ts']} | {r['state']}{tail}")
        text = "\n".join(lines)
    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True

    return False
