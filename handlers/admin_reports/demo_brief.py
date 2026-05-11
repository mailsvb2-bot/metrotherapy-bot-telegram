from __future__ import annotations
import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.demo_analytics import demo_summary, today_range_utc, demo_summary_for_range
from services.demo_analytics import demo_listen_distribution
from services.trial_analytics import trial_conversion_summary, trial_outcome_summary


def _fmt_pct(value) -> str:
    return "—" if value is None else f"{float(value):.1f}%"


def _fmt_delta(value) -> str:
    if value is None:
        return "—"
    v = float(value)
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}"


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    start_utc, end_utc = today_range_utc(settings.TIMEZONE)
    s, t, d, outcome, conv = await asyncio.gather(
        asyncio.to_thread(demo_summary),
        asyncio.to_thread(demo_summary_for_range, start_utc, end_utc),
        asyncio.to_thread(demo_listen_distribution),
        asyncio.to_thread(trial_outcome_summary),
        asyncio.to_thread(trial_conversion_summary),
    )

    by_kind = outcome.get("by_kind") or {}
    work = by_kind.get("work") or {}
    home = by_kind.get("home") or {}

    text = (
        "📊 Демо / Try-before-buy — кратко\n\n"
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
        "Outcome trial (pre/post по source=demo):\n"
        f"— создано trial-сессий: {outcome['total_sessions']}\n"
        f"— дошли до post-оценки: {outcome['completed']} ({_fmt_pct(outcome['completion_pct'])})\n"
        f"— положительная динамика: {outcome['positive']} ({_fmt_pct(outcome['positive_pct'])})\n"
        f"— нейтрально / хуже: {outcome['neutral']} / {outcome['negative']}\n"
        f"— средняя динамика: {_fmt_delta(outcome['avg_delta'])}\n\n"
        "Разрез outcome:\n"
        f"— work: completed={work.get('completed', 0)}, +={work.get('positive', 0)}, avg={_fmt_delta(work.get('avg_delta'))}\n"
        f"— home: completed={home.get('completed', 0)}, +={home.get('positive', 0)}, avg={_fmt_delta(home.get('avg_delta'))}\n\n"
        "Trial → оплата:\n"
        f"— demo users: {conv['demo_users']}\n"
        f"— ack users: {conv['ack_users']} ({_fmt_pct(conv['ack_from_demo_pct'])})\n"
        f"— outcome users: {conv['outcome_users']} ({_fmt_pct(conv['outcome_from_ack_pct'])})\n"
        f"— paid users: {conv['paid_users']} ({_fmt_pct(conv['paid_from_demo_pct'])} от demo)\n\n"
        "ℹ️ Telegram не даёт реальное время прослушивания; длительность — по метаданным файла. "
        "Outcome считается только по собственным pre/post оценкам пользователя."
    )

    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True
