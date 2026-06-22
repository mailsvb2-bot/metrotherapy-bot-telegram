from __future__ import annotations

import asyncio

from aiogram.types import CallbackQuery, Message

from services.events import log_event
from services.personalization import get_preface
from services.payments.ui import kb_back, kb_tariffs

_LEGACY_PAYMENT_DISABLED = (
    "Этот старый способ оплаты отключён. Откройте тарифы заново и выберите актуальный пакет."
)


async def cmd_subscribe(message: Message) -> None:
    await message.answer("💳 Тарифы:", reply_markup=kb_tariffs(message.from_user.id if message.from_user else None))


async def sub_menu(cb: CallbackQuery) -> None:
    """Show the canonical public package checkout surface."""
    preface = await asyncio.to_thread(get_preface, int(cb.from_user.id), "sub")
    text = f"{preface}💳 Выберите пакет практик:"
    await cb.message.edit_text(text, reply_markup=kb_tariffs(cb.from_user.id))
    log_event(cb.from_user.id, "view_tariffs", {"surface": "package_checkout"})
    log_event(cb.from_user.id, "sub_menu_open", {"surface": "package_checkout"})


async def sub_pick(cb: CallbackQuery) -> None:
    """Legacy Telegram invoice tariff picker: intentionally disabled."""
    log_event(cb.from_user.id, "legacy_payment_callback_blocked", {"stage": "sub_pick"})
    await cb.message.answer(_LEGACY_PAYMENT_DISABLED, reply_markup=kb_back("sub:menu"))


async def pay_selected(cb: CallbackQuery) -> None:
    """Legacy Telegram invoice payment: intentionally disabled."""
    log_event(cb.from_user.id, "legacy_payment_callback_blocked", {"stage": "pay_selected"})
    await cb.message.answer(_LEGACY_PAYMENT_DISABLED, reply_markup=kb_back("sub:menu"))
