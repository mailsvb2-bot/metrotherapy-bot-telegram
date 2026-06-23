from __future__ import annotations

import asyncio

from aiogram.types import CallbackQuery, Message

from services.events import log_event
from services.personalization import get_preface
from services.payments.ui import kb_back, kb_tariffs

_LEGACY_PAYMENT_DISABLED = (
    "Этот старый способ оплаты отключён. Откройте тарифы заново и выберите актуальный пакет."
)


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return int(user.id) if user is not None else None


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


def _callback_user_id(cb: CallbackQuery) -> int:
    return int(cb.from_user.id)


async def cmd_subscribe(message: Message) -> None:
    user_id = _message_user_id(message)
    await message.answer("💳 Тарифы:", reply_markup=kb_tariffs(user_id))


async def sub_menu(cb: CallbackQuery) -> None:
    """Show the canonical public package checkout surface."""
    message = _callback_message(cb)
    if message is None:
        return

    user_id = _callback_user_id(cb)
    preface = await asyncio.to_thread(get_preface, user_id, "sub")
    text = f"{preface}💳 Выберите пакет практик:"
    await message.edit_text(text, reply_markup=kb_tariffs(user_id))
    log_event(user_id, "view_tariffs", {"surface": "package_checkout"})
    log_event(user_id, "sub_menu_open", {"surface": "package_checkout"})


async def sub_pick(cb: CallbackQuery) -> None:
    """Legacy Telegram invoice tariff picker: intentionally disabled."""
    message = _callback_message(cb)
    if message is None:
        return

    user_id = _callback_user_id(cb)
    log_event(user_id, "legacy_payment_callback_blocked", {"stage": "sub_pick"})
    await message.answer(_LEGACY_PAYMENT_DISABLED, reply_markup=kb_back("sub:menu"))


async def pay_selected(cb: CallbackQuery) -> None:
    """Legacy Telegram invoice payment: intentionally disabled."""
    message = _callback_message(cb)
    if message is None:
        return

    user_id = _callback_user_id(cb)
    log_event(user_id, "legacy_payment_callback_blocked", {"stage": "pay_selected"})
    await message.answer(_LEGACY_PAYMENT_DISABLED, reply_markup=kb_back("sub:menu"))
