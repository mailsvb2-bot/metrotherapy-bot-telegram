from __future__ import annotations

"""Thin payment router.

Canonical public checkout is the external YooKassa/package URL flow. Legacy
Telegram invoice callback buttons are kept as graceful dead-ends so stale inline
keyboards cannot open a second payment path.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

from services.payments.common import is_user_share_message, safe_answer_callback
from services.payments.gift import gift_menu, gift_pick_cancel, gift_pick_target, gift_users_shared
from services.payments.hooks import pre_checkout, successful_payment
from services.payments.subscription import cmd_subscribe, sub_menu
from services.payments.ui import kb_after_paid, kb_back

router = Router()

_DISABLED = "Этот старый способ оплаты отключён. Откройте тарифы заново и выберите актуальный пакет."


@router.message(F.text == "❌ Отмена")
async def _gift_pick_cancel(message: Message):
    await gift_pick_cancel(message)


@router.message(Command("subscribe"))
async def _cmd_subscribe(message: Message):
    await cmd_subscribe(message)


@router.callback_query(F.data == "sub:menu")
async def _sub_menu(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await sub_menu(cb)


@router.callback_query(F.data.regexp(r"^sub:buy:\d+:\d+$"))
async def _sub_pick_disabled(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await cb.message.answer(_DISABLED, reply_markup=kb_back("sub:menu"))


@router.callback_query(F.data == "pay:selected")
async def _pay_selected_disabled(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await cb.message.answer(_DISABLED, reply_markup=kb_back("sub:menu"))


@router.callback_query(F.data == "gift:menu")
async def _gift_menu(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await gift_menu(cb)


@router.callback_query(F.data == "gift:pick_target")
async def _gift_pick_target(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await gift_pick_target(cb)


@router.message(is_user_share_message)
async def _gift_users_shared(message: Message, state: FSMContext):
    await gift_users_shared(message, state)


@router.callback_query(F.data.regexp(r"^gift:buy:\d+:\d+$"))
async def _gift_buy_disabled(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await cb.message.answer(_DISABLED, reply_markup=kb_back("gift:menu"))


@router.pre_checkout_query()
async def _pre_checkout(pre: PreCheckoutQuery):
    await pre_checkout(pre)


@router.message(F.successful_payment)
async def _successful_payment(message: Message):
    await successful_payment(message)
