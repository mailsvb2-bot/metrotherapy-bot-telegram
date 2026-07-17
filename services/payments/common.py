from __future__ import annotations

import json
import logging
from decimal import Decimal, ROUND_HALF_UP

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError

from config.settings import settings
from services.payments.receipt_contract import validate_receipt_contract

logger = logging.getLogger(__name__)


def money_str_rub(amount_rub: int) -> str:
    """Форматируем сумму в рублях как строку с 2 знаками для YooKassa receipt."""
    # amount_rub у нас int, но держим через Decimal чтобы не поймать float.
    return f"{Decimal(amount_rub).quantize(Decimal('1'), rounding=ROUND_HALF_UP):.2f}"  # type: ignore


def yookassa_provider_data_receipt(title: str, price_rub: int) -> str:
    """provider_data для YooKassa (receipt).

    В Telegram Payments provider_data передаётся как JSON-строка и прокидывается в платёжного провайдера.
    Для многих подключений YooKassa это критично (фискализация 54‑ФЗ).
    """

    value = f"{Decimal(price_rub).quantize(Decimal('1'), rounding=ROUND_HALF_UP):.2f}"  # 10 -> "10.00"
    tax_system_code, vat_code, payment_mode, payment_subject = validate_receipt_contract(
        tax_system_code=getattr(settings, "YOOKASSA_TAX_SYSTEM_CODE", 2),
        vat_code=getattr(settings, "YOOKASSA_VAT_CODE", 1),
        payment_mode=getattr(settings, "YOOKASSA_PAYMENT_MODE", "full_payment"),
        payment_subject=getattr(settings, "YOOKASSA_PAYMENT_SUBJECT", "service"),
    )
    receipt = {
        "receipt": {
            "tax_system_code": tax_system_code,
            "items": [
                {
                    "description": (title or "Подписка").strip()[:128],
                    "quantity": "1.00",
                    "amount": {"value": value, "currency": "RUB"},
                    "vat_code": vat_code,
                    "payment_subject": payment_subject,
                    "payment_mode": payment_mode,
                }
            ],
        }
    }
    return json.dumps(receipt, ensure_ascii=False)


def is_user_share_message(m: Message) -> bool:
    """Ловим ответ на KeyboardButtonRequestUser максимально надёжно."""
    return bool(getattr(m, "user_shared", None) or getattr(m, "users_shared", None))


def invoice_link_kb(url: str, back_cb: str = "menu:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Открыть оплату", url=url)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
    ])


async def safe_answer_callback(cb: CallbackQuery, *args, **kwargs) -> bool:
    """Best-effort callback ack without turning an expired query into 500."""
    try:
        await cb.answer(*args, **kwargs)
        return True
    except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError):
        logger.debug("Callback answer failed", exc_info=True)
        return False
