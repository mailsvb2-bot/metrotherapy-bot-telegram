from __future__ import annotations
import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.gift_share_analytics import report as giftshare_report


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    rep = await asyncio.to_thread(giftshare_report, None, None)

    def _fmt_chain(title: str, chain: list[dict]) -> list[str]:
        lines = [title]
        for x in chain:
            step = str(x.get("step"))
            users = int(x.get("users") or 0)
            pct = x.get("from_prev_pct")
            tail = "" if pct is None else f" ({pct}%)"
            lines.append(f"— {step}: {users}{tail}")
        return lines

    lines = [
        "🎁 Share/Gift — конверсия по шагам (уникальные пользователи)",
        "",
        *_fmt_chain("Цепочка Share:", list(rep.get("share_chain") or [])),
        "",
        *_fmt_chain("Цепочка Gift:", list(rep.get("gift_chain") or [])),
        "",
        "ℹ️ from_prev_pct — доля от предыдущего шага.",
    ]

    await safe_edit(cb, "\n".join(lines), reply_markup=ctx.staff_kb)
    return True
