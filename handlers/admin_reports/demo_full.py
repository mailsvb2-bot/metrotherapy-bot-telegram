from __future__ import annotations
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.demo_analytics import demo_summary_for_range, demo_user_breakdown, today_range_utc
from services.state_log import activity_spans


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    start_utc, end_utc = today_range_utc(settings.TIMEZONE)
    t, top = await asyncio.gather(
        asyncio.to_thread(demo_summary_for_range, start_utc, end_utc),
        asyncio.to_thread(demo_user_breakdown, limit=20),
    )
    user_ids = [x["user_id"] for x in top]
    spans_today = await asyncio.to_thread(activity_spans, user_ids, start_ts=start_utc, end_ts=end_utc)

    tz = ZoneInfo(settings.TIMEZONE)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")

    lines = [
        "📈 Демо — подробно",
        f"Срез: сегодня ({settings.TIMEZONE}), сформировано: {now}",
        "",
        f"Уникальных пользователей сегодня: {t['uniq_users']}",
        f"Отправлено work/home сегодня: {t['sent_work']} / {t['sent_home']}",
        f"Отметили прослушивание work/home: {t['ack_work']} / {t['ack_home']}",
        f"Суммарная длительность (по метаданным): {fmt_sec(t['duration_sum_sec'])}",
        "",
        "Топ-20 по сумме длительностей демо (за всё время) + активность сегодня:",
    ]

    for x in top:
        uid = x["user_id"]
        dur = fmt_sec(x.get("duration_sum_sec"))
        ack = fmt_sec(x.get("avg_ack_delay_sec"))
        span = spans_today.get(uid) or {}
        last = fmt_ts(span.get("last_ts"), tz)
        total = fmt_sec(span.get("total_sec"))
        lines.append(f"• {uid}: dur={dur}, ack={ack}, today={total}, last={last}")

    await safe_edit(cb, "\n".join(lines), reply_markup=ctx.staff_kb)
    return True
