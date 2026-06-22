from __future__ import annotations
import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit

from services.store import store


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    missing, users = await asyncio.gather(
        asyncio.to_thread(store.users_missing_times),
        asyncio.to_thread(store.count_users),
    )
    text = (
        "🧩 Удержание (базово)\n\n"
        f"Пользователей всего: {users}\n"
        f"Не назначили время «на работу»: {missing['missing_work']}\n"
        f"Не назначили время «домой»: {missing['missing_home']}\n\n"
        "Следующий шаг (если нужно): отчёт по «пропустил N дней» по событиям audio_sent."
    )
    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True
