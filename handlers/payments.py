from __future__ import annotations

from core.callback_utils import safe_answer_callback
"""Handlers оплаты.

Этот файл intentionally thin: только маршрутизация aiogram -> services.payments.*
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery
from aiogram.fsm.context import FSMContext

from services.payments.common import is_user_share_message, safe_answer_callback
from services.payments.subscription import cmd_subscribe, sub_menu, sub_pick, pay_selected
from services.payments.gift import gift_menu, gift_pick_target, gift_users_shared, gift_buy, gift_pick_cancel
from services.payments.hooks import pre_checkout, successful_payment
from services.payments.ui import kb_after_paid  # backward-compat for handlers.gift_flow

router = Router()


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
async def _sub_pick(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await sub_pick(cb)


@router.callback_query(F.data == "pay:selected")
async def _pay_selected(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await pay_selected(cb)


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
async def _gift_buy(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await gift_buy(cb)


@router.pre_checkout_query()
async def _pre_checkout(pre: PreCheckoutQuery):
    await pre_checkout(pre)


@router.message(F.successful_payment)
async def _successful_payment(message: Message):
    await successful_payment(message)
