from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message

from services.events import log_event
from services.gift_claims import create_gift_checkout_token
from services.practice_token_contract import PracticePackage, package_by_id, telegram_stars_enabled


PREMIUM_BOT_URL = "https://t.me/PremiumBot"


def _stars_amount_label(amount_xtr: int) -> str:
    return f"{int(amount_xtr):,} Stars".replace(",", " ")


def _invoice_keyboard(
    *,
    url: str,
    amount_xtr: int,
    package_id: str,
    as_gift: bool,
) -> InlineKeyboardMarkup:
    retry_action = "gift" if as_gift else "buy"
    methods_action = "gift_methods" if as_gift else "methods"
    amount = _stars_amount_label(amount_xtr)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ Оплатить пакет — {amount}", url=url)],
            [InlineKeyboardButton(text="➕ Купить Stars в официальном Telegram", url=PREMIUM_BOT_URL)],
            [
                InlineKeyboardButton(
                    text="🔄 Я купил(а) Stars — продолжить",
                    callback_data=f"stars:{retry_action}:{package_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К способам оплаты",
                    callback_data=f"pay:{methods_action}:{package_id}",
                )
            ],
        ]
    )


def _invoice_message_text(*, amount_xtr: int) -> str:
    amount = _stars_amount_label(amount_xtr)
    return (
        f"⭐ Оплата пакета — {amount}\n\n"
        "Если Stars уже есть, нажмите «Оплатить пакет».\n\n"
        "Если Stars не хватает:\n"
        "1. Нажмите «Купить Stars в официальном Telegram».\n"
        f"2. Купите не меньше {amount}.\n"
        "3. Вернитесь в этот чат и нажмите «Я купил(а) Stars — продолжить».\n\n"
        "Покупка Stars проходит на стороне Telegram. "
        "Метротерапия не получает и не хранит данные вашей карты."
    )


async def _legacy_unbound_message_fallback(
    message: Message,
    *,
    title: str,
    description: str,
    payload: str,
    currency: str,
    amount_xtr: int,
    start_parameter: str,
) -> None:
    """Keep isolated unit doubles usable; real aiogram messages always carry a bot."""

    await message.answer_invoice(
        title=title,
        description=description,
        payload=payload,
        currency=currency,
        prices=[LabeledPrice(label=title, amount=amount_xtr)],
        start_parameter=start_parameter,
    )


def _message_bot(message: Message) -> Any | None:
    try:
        return message.bot
    except (AttributeError, RuntimeError):
        return None


async def send_stars_invoice(
    message: Message,
    *,
    package_id: str,
    as_gift: bool = False,
) -> str:
    """Create a native XTR invoice link and send a guided purchase flow.

    Production uses the same ``createInvoiceLink`` method that is exercised by
    the live provider capability audit. Buyers who do not yet have enough Stars
    can open Telegram's official PremiumBot and then request a fresh invoice for
    the same package without repeating package selection or terms navigation.
    """

    from services.payments.telegram_stars import (  # local import avoids package-init cycles
        STARS_CURRENCY,
        StarsPaymentError,
        build_stars_payload,
        parse_stars_payload,
    )

    if not telegram_stars_enabled():
        raise StarsPaymentError("stars_payments_disabled")
    user = message.from_user
    if user is None:
        raise StarsPaymentError("stars_buyer_missing")
    package: PracticePackage = package_by_id(package_id)
    if not package.public:
        raise StarsPaymentError("stars_package_not_public")

    gift_token = ""
    if as_gift:
        gift_token = create_gift_checkout_token(
            buyer_user_id=int(user.id),
            package_id=package.package_id,
            source_platform="telegram",
        )
    payload = build_stars_payload(
        buyer_user_id=int(user.id),
        package_id=package.package_id,
        gift_token=gift_token,
    )
    order = parse_stars_payload(payload)
    description = package.description
    if as_gift:
        description = f"Подарок: {package.description} Получатель активирует пакет по универсальной ссылке."

    title = package.title[:32]
    description = description[:255]
    start_parameter = f"xtr_{package.package_id}"[:64]
    bot = _message_bot(message)
    if bot is None or not callable(getattr(bot, "create_invoice_link", None)):
        await _legacy_unbound_message_fallback(
            message,
            title=title,
            description=description,
            payload=payload,
            currency=STARS_CURRENCY,
            amount_xtr=order.amount_xtr,
            start_parameter=start_parameter,
        )
    else:
        invoice_url = await bot.create_invoice_link(
            title=title,
            description=description,
            payload=payload,
            currency=STARS_CURRENCY,
            prices=[LabeledPrice(label=title, amount=order.amount_xtr)],
        )
        if not isinstance(invoice_url, str) or not invoice_url.startswith("https://"):
            raise StarsPaymentError("stars_invoice_link_invalid")
        await message.answer(
            _invoice_message_text(amount_xtr=order.amount_xtr),
            reply_markup=_invoice_keyboard(
                url=invoice_url,
                amount_xtr=order.amount_xtr,
                package_id=package.package_id,
                as_gift=as_gift,
            ),
        )

    log_event(
        int(user.id),
        "telegram_stars_invoice_created",
        {
            "package_id": package.package_id,
            "gift": bool(as_gift),
            "amount_xtr": order.amount_xtr,
            "transport": "invoice_link" if bot is not None else "unbound_test_fallback",
            "stars_purchase_recovery": True,
        },
    )
    return gift_token


def install_stars_invoice_link_transport() -> None:
    """Install the audited invoice-link transport before payment handlers import it."""

    from services.payments import telegram_stars

    telegram_stars.send_stars_invoice = send_stars_invoice
