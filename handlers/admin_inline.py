
import asyncio
import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove

from config.settings import settings
from handlers import admin_inline_tariffs
from handlers.admin_inline_common import AdminCtx, admin_nav_back, get_staff_roles, is_superadmin, safe_edit_admin
from handlers.admin_inline_perms import handle as handle_perms
from handlers.admin_inline_roles import handle as handle_roles
from handlers.admin_inline_users import handle as handle_users
from handlers.admin_inline_reports import handle as handle_reports
from handlers.admin_inline_copy import handle as handle_copy
from handlers.admin_inline_states import AdminManageState
from keyboards.inline import kb_staff_menu
from services.admin import is_admin
from services.roles import ROLE_ADMIN


router = Router()


@router.callback_query(lambda c: (c.data or "").startswith("admin:"))
async def admin_gate(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id if cb.from_user else None
    if not is_admin(uid):
        try:
            await cb.answer("Недоступно.", show_alert=True)
        except (TelegramAPIError, asyncio.TimeoutError):
            logging.getLogger(__name__).exception("Unhandled exception")
        return

    roles = get_staff_roles(int(uid))
    if not roles:
        return await cb.answer("", show_alert=False)

    from services.admin_permissions import get_allowed_perms

    allowed = None
    if not is_superadmin(int(uid)):
        allowed = get_allowed_perms(int(uid))

    staff_kb = kb_staff_menu(roles, is_superadmin=is_superadmin(int(uid)), allowed_perms=allowed)
    ctx = AdminCtx(uid=int(uid), roles=roles, staff_kb=staff_kb, is_superadmin=is_superadmin(int(uid)), allowed_perms=allowed)

    data = cb.data or ""

    # Back navigation (single-message admin UI)
    if await admin_nav_back(cb, state):
        return

    # 1) Tariffs (already extracted module)
    tariffs_ctx = admin_inline_tariffs.TariffsCtx(
        is_superadmin=ctx.is_superadmin,
        can_manage_tariffs=(ctx.is_superadmin or ROLE_ADMIN in roles),
        staff_kb=staff_kb,
    )
    if await admin_inline_tariffs.handle_tariffs_callback(cb, state, data, tariffs_ctx):
        return

    # 2) Other sections
    if await handle_copy(cb, state, data, ctx):
        return
    if await handle_perms(cb, state, data, ctx):
        return
    if await handle_roles(cb, state, data, ctx):
        return
    if await handle_users(cb, state, data, ctx):
        return
    if await handle_reports(cb, state, data, ctx):
        return

    # Menu
    if data == "admin:menu":
        await safe_edit_admin(
            cb,
            state,
            "🛠 Админ-панель\n\nВыберите раздел:",
            reply_markup=staff_kb,
            push=False,
            reset_stack=True,
        )
        return

    await cb.answer("", show_alert=False)


@router.message(AdminManageState.waiting_tariffs_text)
async def admin_tariffs_input(msg: Message, state: FSMContext):
    uid = msg.from_user.id if msg.from_user else None
    await admin_inline_tariffs.admin_tariffs_input(msg, state, admin_id=uid)


@router.message(AdminManageState.waiting_tariff_single_price)
async def admin_tariff_single_price_input(msg: Message, state: FSMContext):
    uid = msg.from_user.id if msg.from_user else None
    await admin_inline_tariffs.admin_tariff_single_price_input(msg, state, admin_id=uid)


@router.message(AdminManageState.waiting_admin_user)
async def admin_add_admin_input(msg: Message, state: FSMContext):
    """Add admin by Telegram picker, forwarded message, @username, or numeric user_id."""
    uid = msg.from_user.id if msg.from_user else None
    if not is_admin(uid):
        return
    text = (msg.text or "").strip()

    if text.lower() in {"отмена", "cancel", "/cancel"}:
        await state.clear()
        await msg.answer("Ок, отменено.", reply_markup=ReplyKeyboardRemove())
        return

    target_id: int | None = None

    # 1) Telegram user picker (request_user)
    user_shared = getattr(msg, "user_shared", None)
    if user_shared and getattr(user_shared, "user_id", None):
        try:
            target_id = int(user_shared.user_id)
        except (TypeError, ValueError):
            target_id = None

    # 2) Forwarded message
    if target_id is None:
        fwd = getattr(msg, "forward_from", None)
        if fwd and getattr(fwd, "id", None):
            try:
                target_id = int(fwd.id)
            except (TypeError, ValueError):
                target_id = None

    # 3) @username -> resolve via get_chat
    if target_id is None and text.startswith("@") and len(text) > 1:
        username = text
        try:
            chat = await msg.bot.get_chat(username)
            target_id = int(chat.id)
        except (TelegramAPIError, TelegramBadRequest):
            target_id = None
        except (ValueError, TypeError):
            target_id = None

    # 4) numeric id
    if target_id is None and text:
        try:
            if text.isdigit():
                target_id = int(text)
        except (TelegramAPIError, TelegramBadRequest):
            target_id = None
        except (ValueError, TypeError):
            target_id = None

    if target_id is None:
        await msg.answer(
            "Не понял кого добавить.\n\n"
            "Варианты:\n"
            "• нажмите «Выбрать пользователя»\n"
            "• перешлите сообщение от человека\n"
            "• отправьте @username\n"
            "• или отправьте числом user_id\n\n"
            "Отмена — напишите «Отмена».",
        )
        return

    try:
        from services.roles import grant_role
        grant_role(int(target_id), ROLE_ADMIN)
    except RuntimeError:
        logging.getLogger(__name__).exception("Failed to add admin")
    except OSError:
        logging.getLogger(__name__).exception("Failed to add admin")
    except (ValueError, TypeError):
        logging.getLogger(__name__).exception("Failed to add admin")
        await msg.answer("Не удалось добавить администратора (ошибка).", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return

    await state.clear()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Роли команды", callback_data="admin:roles:list")],
            [InlineKeyboardButton(text="🔐 Доступы админов", callback_data="admin:perms")],
            [InlineKeyboardButton(text="🏠 Админ-меню", callback_data="admin:menu")],
        ]
    )
    await msg.answer(
        f"✅ Добавил администратора: {int(target_id)}\n\n"
        "Теперь можно назначить роли и настроить доступы.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await msg.answer("Куда дальше?", reply_markup=kb)
