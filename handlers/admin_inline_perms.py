from __future__ import annotations

import asyncio
import logging

import sqlite3
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUser,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from handlers.admin_inline_common import AdminCtx, safe_edit_admin
from handlers.admin_inline_states import AdminManageState


async def handle(cb: CallbackQuery, state: FSMContext, data: str, ctx: AdminCtx) -> bool:
    log = logging.getLogger(__name__)

    if data == "admin:perms":
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
            return True

        from services.admin_permissions import list_admin_ids

        admin_ids = list_admin_ids()
        if not admin_ids:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")]]
            )
            await safe_edit_admin(cb, state, "🔐 Доступы админов\n\nПока нет администраторов с ролями.", reply_markup=kb)
            return True

        rows: list[list[InlineKeyboardButton]] = []
        for uid in admin_ids:
            rows.append([InlineKeyboardButton(text=str(uid), callback_data=f"admin:perms:user:{uid}")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        await safe_edit_admin(cb, state, "🔐 Выберите администратора:", reply_markup=kb)
        return True

    if data.startswith("admin:perms:user:"):
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
            return True
        try:
            target_id = int(data.split(":")[-1])
        except ValueError:
            await cb.answer("", show_alert=False)
            return True

        from services.admin_permissions import PERMS, get_allowed_perms

        allowed = get_allowed_perms(target_id)
        allowed_set = allowed or set()

        buttons: list[list[InlineKeyboardButton]] = []
        for item in PERMS:
            mark = "✅" if (allowed is None or item.perm in allowed_set) else "⬜️"
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{mark} {item.title}",
                        callback_data=f"admin:perms:toggle:{target_id}:{item.perm}",
                    )
                ]
            )
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:perms")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await safe_edit_admin(
            cb,
            state,
            f"🔐 Права администратора {target_id}\n\nНажимайте, чтобы включить/выключить.",
            reply_markup=kb,
        )
        return True

    if data.startswith("admin:perms:toggle:"):
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
            return True
        parts = data.split(":", 4)
        if len(parts) < 5:
            await cb.answer("", show_alert=False)
            return True
        try:
            target_id = int(parts[3])
        except ValueError:
            await cb.answer("", show_alert=False)
            return True
        perm = parts[4]

        from services.admin_permissions import PERMS, get_allowed_perms, set_perm, toggle_perm

        # If no explicit restrictions exist, persist "all allowed" first, then toggle the specific one.
        if get_allowed_perms(target_id) is None:
            for item in PERMS:
                set_perm(target_id, item.perm, True, updated_by=int(ctx.uid))
        toggle_perm(target_id, perm, updated_by=int(ctx.uid))

        # Re-render the same screen (no recursion to admin_inline()).
        return await handle(cb, state, f"admin:perms:user:{target_id}", ctx)

    if data == "admin:add_admin":
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
            return True

        await state.clear()
        await state.set_state(AdminManageState.waiting_admin_user)

        kb = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text="Выбрать пользователя",
                        request_user=KeyboardButtonRequestUser(request_id=1, user_is_bot=False),
                    ),
                    KeyboardButton(text="Отмена"),
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await cb.message.answer(
            "👥 Добавить администратора\n\n"
            "Можно любым способом:\n"
            "• Нажмите «Выбрать пользователя» (пикер Telegram)\n"
            "• Или просто перешлите сюда сообщение от нужного человека\n"
            "• Или отправьте @username\n"
            "• Или отправьте числом его user_id\n\n"
            "Отмена — кнопкой ниже.",
            reply_markup=kb,
        )
        return True

    if data.startswith("admin:add_admin_role:"):
        if not ctx.is_superadmin:
            await cb.answer("", show_alert=False)
            return True
        parts = data.split(":")
        if len(parts) != 4:
            await cb.answer("", show_alert=False)
            return True
        role = parts[2]
        try:
            target_id = int(parts[3])
        except ValueError:
            await cb.answer("", show_alert=False)
            return True

        try:
            from services.roles import grant_role
            grant_role(target_id, role)
        except ImportError:
            log.exception("Не удалось выдать роль")
            await cb.answer("Ошибка", show_alert=True)
            return True
        except (sqlite3.Error, ValueError, TypeError):
            log.exception("Не удалось выдать роль")
            await cb.answer("Ошибка", show_alert=True)
            return True

        await state.clear()
        await cb.message.answer(
            f"✅ Пользователю {target_id} назначена роль: {role}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return True

    return False