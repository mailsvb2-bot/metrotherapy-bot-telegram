from __future__ import annotations
import logging


from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit_admin
from handlers.text_input import AdminInputState
from services.admin_cards import users_joined_today, users_joined_today_count


async def handle(cb: CallbackQuery, state: FSMContext, data: str, ctx: AdminCtx) -> bool:
    if data == "admin:users:today":
        n = users_joined_today_count()
        rows = users_joined_today(limit=25)
        lines = [f"👥 Пользователи сегодня (UTC): {n}\n"]
        for r in rows:
            uid = r.get("user_id")
            uname = (r.get("username") or "").strip()
            name = (r.get("first_name") or "").strip()
            tail = ""
            if uname:
                tail += f"@{uname} "
            if name:
                tail += name
            lines.append(f"• {uid} {tail}".rstrip())
        await safe_edit_admin(cb, state, "\n".join(lines), reply_markup=ctx.staff_kb)
        return True

    if data == "admin:user:card":
        await state.set_state(AdminInputState.user_card)
        await cb.message.answer(
            "🔎 Карточка пользователя\n\n"
            "Пожалуйста, отправьте user_id (числом).\n"
            "Например: 123456789",
        )
        return True

    return False
