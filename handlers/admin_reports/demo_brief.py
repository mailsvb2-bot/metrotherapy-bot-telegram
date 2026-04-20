from __future__ import annotations
import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.demo_analytics import demo_summary, today_range_utc, demo_summary_for_range
from services.demo_analytics import demo_listen_distribution


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    start_utc, end_utc = today_range_utc(settings.TIMEZONE)
    s, t, d = await asyncio.gather(
        asyncio.to_thread(demo_summary),
        asyncio.to_thread(demo_summary_for_range, start_utc, end_utc),
        asyncio.to_thread(demo_listen_distribution),
    )

    text = (
        "📊 Демо — кратко\n\n"
        f"Сегодня ({settings.TIMEZONE}):\n"
        f"— уникальных пользователей: {t['uniq_users']}\n"
        f"— отправлено work/home: {t['sent_work']} / {t['sent_home']}\n"
        f"— отметили прослушивание work/home: {t['ack_work']} / {t['ack_home']}\n"
        f"— суммарная длительность (по метаданным): {fmt_sec(t['duration_sum_sec'])}\n\n"
        "За всё время:\n"
        f"— отправлено work/home: {s['sent_work']} / {s['sent_home']}\n"
        f"— отметили прослушивание work/home: {s['ack_work']} / {s['ack_home']}\n"
        f"— прослушали оба (по отметкам): {s['both_acked_users']}\n"
        f"— среднее время до отметки: {fmt_sec(s['avg_ack_delay_sec'])}\n\n"
        "Распределение отметок (все время):\n"
        f"— получили демо (уник.): {d['sent_users']}\n"
        f"— отметили хотя бы одно: {d['acked_users']}\n"
        f"— отметили 1 демо: {d['acked_one']}\n"
        f"— отметили 2 демо: {d['acked_two']}\n\n"
        "ℹ️ Telegram не даёт реальное время прослушивания; длительность — по метаданным файла."
    )

    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True
