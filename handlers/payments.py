from __future__ import annotations

"""Payment router.

Telegram users can choose either native Telegram Stars or the existing external
YooKassa checkout. VK, MAX and web checkout remain on YooKassa. Legacy Telegram
RUB invoice callback buttons stay disabled so they cannot create a third payment
path.
"""

import asyncio
import logging
import sqlite3

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

from services.payments.common import is_user_share_message, safe_answer_callback
from services.payments.gift import (
    deliver_gift_message,
    gift_menu,
    gift_pick_cancel,
    gift_pick_target,
    gift_users_shared,
)
from services.payments.hooks import (
    pre_checkout as legacy_pre_checkout,
    successful_payment as legacy_successful_payment,
)
from services.payments.subscription import cmd_subscribe, sub_menu
from services.payments.telegram_stars import (
    STARS_CURRENCY,
    StarsPaymentError,
    record_successful_stars_payment,
    send_stars_invoice,
    validate_stars_pre_checkout,
)
from services.payments.ui import kb_after_paid, kb_back

router = Router()
log = logging.getLogger(__name__)

_DISABLED = "Этот старый способ оплаты отключён. Откройте тарифы заново и выберите актуальный пакет."


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


def _package_id(data: str | None, prefix: str) -> str:
    raw = str(data or "")
    if not raw.startswith(prefix):
        raise ValueError("stars_package_callback_invalid")
    package_id = raw[len(prefix):].strip()
    if not package_id:
        raise ValueError("stars_package_callback_invalid")
    return package_id


async def _send_stars_from_callback(cb: CallbackQuery, *, as_gift: bool) -> None:
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return
    prefix = "stars:gift:" if as_gift else "stars:buy:"
    try:
        package_id = _package_id(cb.data, prefix)
        await send_stars_invoice(message, package_id=package_id, as_gift=as_gift)
    except (StarsPaymentError, ValueError):
        log.exception("Telegram Stars invoice creation failed")
        await message.answer(
            "Не удалось создать счёт в Stars. Выберите пакет ещё раз или оплатите через YooKassa.",
            reply_markup=kb_back("sub:menu" if not as_gift else "gift:menu"),
        )
    except (TelegramAPIError, asyncio.TimeoutError):
        log.exception("Telegram Stars API invoice request failed")
        await message.answer(
            "Telegram временно не создал счёт в Stars. YooKassa продолжает работать — можно выбрать её в списке пакетов.",
            reply_markup=kb_back("sub:menu" if not as_gift else "gift:menu"),
        )


@router.message(F.text == "❌ Отмена")
async def _gift_pick_cancel(message: Message):
    await gift_pick_cancel(message)


@router.message(Command("subscribe"))
async def _cmd_subscribe(message: Message):
    await cmd_subscribe(message)


@router.message(Command("paysupport"))
async def _pay_support(message: Message):
    await message.answer(
        "Поддержка по оплате\n\n"
        "Напишите @metrotherapysupportbot и приложите:\n"
        "• дату и примерное время оплаты;\n"
        "• выбранный пакет;\n"
        "• способ оплаты: Telegram Stars или YooKassa;\n"
        "• скриншот чека, если он есть.\n\n"
        "Не отправляйте данные банковской карты, коды из SMS и пароли."
    )


@router.callback_query(F.data == "sub:menu")
async def _sub_menu(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await sub_menu(cb)


@router.callback_query(F.data.regexp(r"^stars:buy:[a-z0-9_]+$"))
async def _stars_buy(cb: CallbackQuery):
    await _send_stars_from_callback(cb, as_gift=False)


@router.callback_query(F.data.regexp(r"^stars:gift:[a-z0-9_]+$"))
async def _stars_gift(cb: CallbackQuery):
    await _send_stars_from_callback(cb, as_gift=True)


@router.callback_query(F.data.regexp(r"^sub:buy:\d+:\d+$"))
async def _sub_pick_disabled(cb: CallbackQuery):
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return
    await message.answer(_DISABLED, reply_markup=kb_back("sub:menu"))


@router.callback_query(F.data == "pay:selected")
async def _pay_selected_disabled(cb: CallbackQuery):
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return
    await message.answer(_DISABLED, reply_markup=kb_back("sub:menu"))


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
    message = _callback_message(cb)
    if message is None:
        return
    await message.answer(_DISABLED, reply_markup=kb_back("gift:menu"))


@router.pre_checkout_query()
async def _pre_checkout(pre: PreCheckoutQuery):
    if str(pre.currency or "").upper() != STARS_CURRENCY:
        await legacy_pre_checkout(pre)
        return
    error = await asyncio.to_thread(
        validate_stars_pre_checkout,
        payload=pre.invoice_payload,
        user_id=int(pre.from_user.id),
        currency=pre.currency,
        total_amount=pre.total_amount,
    )
    try:
        await pre.answer(ok=error is None, error_message=error)
    except (TelegramAPIError, asyncio.TimeoutError):
        log.exception("Telegram Stars pre-checkout answer failed")


@router.message(F.successful_payment)
async def _successful_payment(message: Message):
    payment = message.successful_payment
    if payment is None or str(payment.currency or "").upper() != STARS_CURRENCY:
        await legacy_successful_payment(message)
        return
    user = message.from_user
    if user is None:
        return
    try:
        result = await asyncio.to_thread(
            record_successful_stars_payment,
            user_id=int(user.id),
            payload=str(payment.invoice_payload or ""),
            total_amount=int(payment.total_amount or 0),
            currency=str(payment.currency or ""),
            telegram_charge_id=str(payment.telegram_payment_charge_id or ""),
            provider_charge_id=str(payment.provider_payment_charge_id or ""),
        )
    except (StarsPaymentError, ValueError, RuntimeError, OSError, sqlite3.Error):
        log.exception("Telegram Stars payment requires manual recovery")
        await message.answer(
            "Оплата в Stars получена, но автоматическое начисление не завершилось. "
            "Пожалуйста, отправьте /paysupport — платёж сохранён и не потеряется."
        )
        return

    if result.duplicate:
        return
    if result.gift_token:
        code = result.gift_token.removeprefix("gift_")
        await deliver_gift_message(message, code)
        return

    balance = ""
    if result.wallet_balance is not None:
        balance = f" На балансе: {result.wallet_balance} практик."
    await message.answer(
        f"✅ Оплата Telegram Stars прошла. Практики начислены.{balance}\n\n"
        "YooKassa остаётся доступна как альтернативный способ оплаты.",
        reply_markup=kb_after_paid(),
    )
