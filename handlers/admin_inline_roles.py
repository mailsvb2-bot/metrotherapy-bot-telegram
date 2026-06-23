from __future__ import annotations

import asyncio
import logging
import sqlite3

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from handlers.admin_inline_common import AdminCtx, safe_edit_admin
from config.settings import ADMIN_IDS
from handlers.text_input import RolesInputState
from services.roles import ALL_ROLES


from core.callback_utils import safe_answer_callback


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


def _role_users_sync() -> list[tuple[int, set[str]]]:
    from services.admin_permissions import list_admin_ids
    from services.roles import user_roles

    ids = set(int(x) for x in (ADMIN_IDS or []))
    for x in list_admin_ids():
        ids.add(int(x))
    return [(uid, set(user_roles(uid) or set())) for uid in sorted(ids)]


def _user_roles_sync(target_id: int) -> set[str]:
    from services.roles import user_roles

    return set(user_roles(int(target_id)) or set())


def _toggle_role_sync(target_id: int, role: str) -> None:
    from services.roles import grant_role, revoke_role, user_roles

    current = set(user_roles(int(target_id)) or set())
    if role in current:
        revoke_role(int(target_id), role)
    else:
        grant_role(int(target_id), role)


def _role_holders_sync(role: str) -> list[int]:
    from services.roles import list_role_holders

    return list(list_role_holders(str(role)))


async def handle(cb: CallbackQuery, state: FSMContext, data: str, ctx: AdminCtx) -> bool:
    if data == "admin:roles:menu":
        if not ctx.is_superadmin:
            await safe_answer_callback(cb, "", show_alert=False)
            return True

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Назначить роль", callback_data="admin:roles:pick")],
                [InlineKeyboardButton(text="👥 Список админов", callback_data="admin:roles:list")],
                [InlineKeyboardButton(text="📚 По ролям", callback_data="admin:roles:by_role")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")],
            ]
        )
        await safe_edit_admin(
            cb,
            state,
            "👥 Роли команды\n\n"
            "Теперь это делается кнопками: выбираешь администратора → назначаешь/снимаешь роли.\n\n"
            "Роль может быть у нескольких людей, и одному человеку можно выдать несколько ролей.\n"
            "Назначать/снимать можно неограниченное количество раз.",
            reply_markup=kb,
        )
        return True

    if data in {"admin:roles:list", "admin:roles:pick"}:
        if not ctx.is_superadmin:
            await safe_answer_callback(cb, "", show_alert=False)
            return True

        role_users = await asyncio.to_thread(_role_users_sync)

        rows: list[list[InlineKeyboardButton]] = []
        for uid, rs in role_users[:150]:
            tail = (" — " + ",".join(sorted(rs))) if rs else ""
            rows.append([InlineKeyboardButton(text=f"{uid}{tail}", callback_data=f"admin:roles:user:{uid}")])
        rows.append([InlineKeyboardButton(text="➕ Добавить администратора", callback_data="admin:add_admin")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:roles:menu")])

        await safe_edit_admin(cb, state, "👥 Выберите администратора:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return True

    if data.startswith("admin:roles:user:"):
        if not ctx.is_superadmin:
            await safe_answer_callback(cb, "", show_alert=False)
            return True
        try:
            target_id = int(data.split(":")[-1])
        except ValueError:
            await safe_answer_callback(cb, "", show_alert=False)
            return True

        current = await asyncio.to_thread(_user_roles_sync, target_id)
        rows: list[list[InlineKeyboardButton]] = []
        for role in ALL_ROLES:
            mark = "✅" if role in current else "⬜️"
            rows.append([InlineKeyboardButton(text=f"{mark} {role}", callback_data=f"admin:roles:toggle:{target_id}:{role}")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:roles:list")])
        rows.append([InlineKeyboardButton(text="🏠 Админ-меню", callback_data="admin:menu")])
        await safe_edit_admin(
            cb,
            state,
            f"👤 Администратор {target_id}\n\nНажимайте, чтобы назначить/снять роль:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return True

    if data.startswith("admin:roles:toggle:"):
        if not ctx.is_superadmin:
            await safe_answer_callback(cb, "", show_alert=False)
            return True
        parts = data.split(":")
        if len(parts) < 5:
            await safe_answer_callback(cb, "", show_alert=False)
            return True
        try:
            target_id = int(parts[3])
        except ValueError:
            await safe_answer_callback(cb, "", show_alert=False)
            return True
        role = str(parts[4]).strip().lower()

        try:
            await asyncio.to_thread(_toggle_role_sync, target_id, role)
        except (sqlite3.Error, TypeError, ValueError):
            logging.getLogger(__name__).exception("Role toggle failed")
            await safe_answer_callback(cb, "Ошибка", show_alert=True)
            return True

        return await handle(cb, state, f"admin:roles:user:{target_id}", ctx)

    if data == "admin:roles:by_role":
        if not ctx.is_superadmin:
            await safe_answer_callback(cb, "", show_alert=False)
            return True
        rows: list[list[InlineKeyboardButton]] = []
        for role in ALL_ROLES:
            rows.append([InlineKeyboardButton(text=role, callback_data=f"admin:roles:role:{role}")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:roles:menu")])
        await safe_edit_admin(cb, state, "📚 Роли — выберите роль:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return True

    if data.startswith("admin:roles:role:"):
        if not ctx.is_superadmin:
            await safe_answer_callback(cb, "", show_alert=False)
            return True
        role = str(data.split(":")[-1]).strip().lower()
        holders = await asyncio.to_thread(_role_holders_sync, role)
        if not holders:
            text = f"📚 Роль {role}\n\nПока никому не назначена."
        else:
            text = "📚 Роль {r}\n\n".format(r=role) + "\n".join([f"• {u}" for u in holders])
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Назначить кому-то", callback_data="admin:roles:pick")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:roles:by_role")],
                [InlineKeyboardButton(text="🏠 Админ-меню", callback_data="admin:menu")],
            ]
        )
        await safe_edit_admin(cb, state, text, reply_markup=kb)
        return True

    if data == "admin:roles:grant":
        if not ctx.is_superadmin:
            await safe_answer_callback(cb, "", show_alert=False)
            return True
        await state.clear()
        await state.set_state(RolesInputState.grant)
        message = _callback_message(cb)
        if message is None:
            return True
        await message.answer(
            "➕ Выдать роль\n\n"
            "Отправьте: <user_id> <role>\n"
            "Роли: admin / support / marketing",
        )
        return True

    if data == "admin:roles:revoke":
        if not ctx.is_superadmin:
            await safe_answer_callback(cb, "", show_alert=False)
            return True
        await state.clear()
        await state.set_state(RolesInputState.revoke)
        message = _callback_message(cb)
        if message is None:
            return True
        await message.answer(
            "➖ Снять роль\n\n"
            "Отправьте: <user_id> <role>\n"
            "Роли: admin / support / marketing",
        )
        return True

    return False
