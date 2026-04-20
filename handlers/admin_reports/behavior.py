from __future__ import annotations
import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config.settings import settings
from handlers.admin_inline_common import AdminCtx, fmt_sec, fmt_ts, safe_edit

from services.admin_behavior import behavior_report


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    rep = await asyncio.to_thread(behavior_report, days=7)
    dist = rep["dist"]
    pct = rep["pct"]
    lines = [
        "🧠 Поведение (ритм взаимодействия)",
        "",
        "Сегменты по ритму (вся база):",
        f"— сжатый (A): {dist.get('compressed',0)} ({pct.get('compressed',0)}%)",
        f"— разреженный (B): {dist.get('sparse',0)} ({pct.get('sparse',0)}%)",
        f"— стабильный (C): {dist.get('stable',0)} ({pct.get('stable',0)}%)",
        "",
        f"Последние 7 дней (с {rep['since']} UTC):",
        f"— событий взаимодействия: {sum(rep['interactions'].values())}",
        f"— callbacks: {rep['interactions'].get('callback',0)}",
        f"— commands: {rep['interactions'].get('command',0)}",
        f"— messages: {rep['interactions'].get('message',0)}",
        "",
        f"— показано кирпичиков: {rep['bricks']}",
        f"— ответов на микровопросы: {rep['micro_answers']}",
        f"— открыли тарифы (уник.): {rep['sub_menu_open_users']}",
        "",
        "ℹ️ Это не диагностика. Система лишь подстраивает темп и порядок сообщений: сначала подстройка, затем мягкая коррекция.",
    ]
    await safe_edit(cb, "\n".join(lines), reply_markup=ctx.staff_kb)
    return True
