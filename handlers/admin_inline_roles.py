from __future__ import annotations

import logging
import sqlite3

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.admin_inline_common import AdminCtx, safe_edit_admin
from config.settings import ADMIN_IDS
from handlers.text_input import RolesInputState
from services.admin_permissions import list_admin_ids
from services.roles import ALL_ROLES, grant_role, revoke_role, user_roles, list_role_holders


async def handle(cb: CallbackQuery, state: FSMContext, data: str, ctx: AdminCtx) -> bool:
    if data == "admin:roles:menu":
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
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
            await cb.answer("", show_alert=False)
            return True

        ids = set(int(x) for x in (ADMIN_IDS or []))
        for x in list_admin_ids():
            ids.add(int(x))
        ids_list = sorted(ids)

        rows: list[list[InlineKeyboardButton]] = []
        for uid in ids_list[:150]:
            rs = user_roles(uid)
            tail = (" — " + ",".join(sorted(rs))) if rs else ""
            rows.append([InlineKeyboardButton(text=f"{uid}{tail}", callback_data=f"admin:roles:user:{uid}")])
        rows.append([InlineKeyboardButton(text="➕ Добавить администратора", callback_data="admin:add_admin")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:roles:menu")])

        await safe_edit_admin(cb, state, "👥 Выберите администратора:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return True

    if data.startswith("admin:roles:user:"):
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
            return True
        try:
            target_id = int(data.split(":")[-1])
        except ValueError:
            await cb.answer("", show_alert=False)
            return True

        current = user_roles(target_id)
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
            await cb.answer("", show_alert=False)
            return True
        parts = data.split(":")
        if len(parts) < 5:
            await cb.answer("", show_alert=False)
            return True
        try:
            target_id = int(parts[3])
        except ValueError:
            await cb.answer("", show_alert=False)
            return True
        role = str(parts[4]).strip().lower()

        cur = user_roles(target_id)
        try:
            if role in cur:
                revoke_role(target_id, role)
            else:
                grant_role(target_id, role)
        except (sqlite3.Error, TypeError, ValueError):
            logging.getLogger(__name__).exception("Role toggle failed")
            await cb.answer("Ошибка", show_alert=True)
            return True

        return await handle(cb, state, f"admin:roles:user:{target_id}", ctx)

    if data == "admin:roles:by_role":
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
            return True
        rows: list[list[InlineKeyboardButton]] = []
        for role in ALL_ROLES:
            rows.append([InlineKeyboardButton(text=role, callback_data=f"admin:roles:role:{role}")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:roles:menu")])
        await safe_edit_admin(cb, state, "📚 Роли — выберите роль:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return True

    if data.startswith("admin:roles:role:"):
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
            return True
        role = str(data.split(":")[-1]).strip().lower()
        holders = list_role_holders(role)
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
            await cb.answer("", show_alert=False)
            return True
        await state.clear()
        await state.set_state(RolesInputState.grant)
        await cb.message.answer(
            "➕ Выдать роль\n\n"
            "Отправьте: <user_id> <role>\n"
            "Роли: admin / support / marketing",
        )
        return True

    if data == "admin:roles:revoke":
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
            return True
        await state.clear()
        await state.set_state(RolesInputState.revoke)
        await cb.message.answer(
            "➖ Снять роль\n\n"
            "Отправьте: <user_id> <role>\n"
            "Роли: admin / support / marketing",
        )
        return True

    return False
