from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message

from services.events import log_event
from services.gift_claims import create_gift_checkout_token
from services.payments.stars_links import stars_amount_label, stars_topup_url
from services.practice_token_contract import PracticePackage, package_by_id, telegram_stars_enabled


def _stars_amount_label(amount_xtr: int) -> str:
    """Compatibility wrapper for older imports and focused unit tests."""

    return stars_amount_label(amount_xtr)


def _stars_topup_url(*, amount_xtr: int, package_id: str) -> str:
    """Compatibility wrapper for older imports and focused unit tests."""

    return stars_topup_url(amount_xtr=amount_xtr, package_id=package_id)


def _invoice_keyboard(
    *,
    url: str,
    amount_xtr: int,
    package_id: str,
    as_gift: bool,
) -> InlineKeyboardMarkup:
    methods_action = "gift_methods" if as_gift else "methods"
    amount = stars_amount_label(amount_xtr)
    object_name = "подарок" if as_gift else "Метротерапию"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ Оплатить {object_name} — {amount}", url=url)],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад: купить или проверить Stars",
                    callback_data=f"pay:{methods_action}:{package_id}",
                )
            ],
        ]
    )


def _invoice_message_text(*, amount_xtr: int) -> str:
    amount = stars_amount_label(amount_xtr)
    return (
        f"⭐ Счёт готов — {amount}\n\n"
        "Нажмите кнопку оплаты ниже. Telegram покажет окончательное подтверждение, "
        "и Stars спишутся только после Вашего подтверждения.\n\n"
        "Если Stars не хватает, вернитесь назад и выберите «Сначала купить Stars»."
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
    """Create a native XTR invoice only after the buyer chose the payment path.

    Buyers without Stars are sent to Telegram's top-up flow before this function
    is called. The invoice screen therefore stays focused on one action: confirm
    payment. A back button returns to the explicit "already have / buy first"
    choice without losing the selected package.
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
            "stars_purchase_help": "pre_invoice_topup_choice",
        },
    )
    return gift_token


def install_stars_invoice_link_transport() -> None:
    """Install the audited invoice-link transport before payment handlers import it."""

    from services.payments import telegram_stars

    telegram_stars.send_stars_invoice = send_stars_invoice
