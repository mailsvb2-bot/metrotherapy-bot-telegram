from __future__ import annotations

import os

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def payment_terms_url() -> str:
    return (os.getenv("PAYMENT_TERMS_URL") or "https://metrotherapy.ru/terms").strip()


def payment_support_contact() -> str:
    return (os.getenv("PAYMENT_SUPPORT_CONTACT") or "@metrotherapysupportbot").strip()


def payment_terms_text() -> str:
    support = payment_support_contact()
    url = payment_terms_url()
    return (
        "📜 Условия оплаты Telegram Stars\n\n"
        "• Вы приобретаете цифровой пакет практик Метротерапии.\n"
        "• Оплата внутри Telegram проводится только в Telegram Stars (XTR).\n"
        "• Количество практик и состав пакета указаны до подтверждения платежа.\n"
        "• Практики начисляются после получения ботом подтверждения successful_payment.\n"
        "• Повторное подтверждение одного платежа не приводит к повторному начислению.\n"
        "• По вопросам оплаты и возврата используйте /paysupport.\n"
        f"• Поддержка: {support}.\n"
        f"• Полные условия: {url}\n\n"
        "Нажимая «Принимаю и оплатить», Вы подтверждаете, что прочитали и принимаете условия."
    )


def payment_terms_keyboard(*, package_id: str, as_gift: bool) -> InlineKeyboardMarkup:
    action = "gift" if as_gift else "buy"
    rows = [
        [
            InlineKeyboardButton(
                text="✅ Принимаю и оплатить",
                callback_data=f"stars:{action}:{package_id}",
            )
        ],
    ]
    url = payment_terms_url()
    if url.startswith(("https://", "http://")):
        rows.append([InlineKeyboardButton(text="📄 Полные условия", url=url)])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="gift:menu" if as_gift else "sub:menu",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
