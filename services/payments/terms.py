from __future__ import annotations

import html
import os

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.payments.public_url import payment_public_base_url


def payment_terms_url() -> str:
    configured = (os.getenv("PAYMENT_TERMS_URL") or "").strip()
    base_url = payment_public_base_url()
    if configured.rstrip("/") == "https://metrotherapy.ru/terms" and base_url:
        return f"{base_url}/terms"
    if configured:
        return configured
    return f"{base_url}/terms" if base_url else ""


def payment_support_contact() -> str:
    return (os.getenv("PAYMENT_SUPPORT_CONTACT") or "@metrotherapysupportbot").strip()


def payment_merchant_name() -> str:
    return (os.getenv("PAYMENT_MERCHANT_NAME") or "Метротерапия").strip()


def payment_terms_text() -> str:
    """Telegram-facing terms: digital packages inside Telegram are Stars-only."""

    support = payment_support_contact()
    url = payment_terms_url()
    terms_line = f"• Полные условия: {url}\n" if url else ""
    return (
        "📜 Условия оплаты\n\n"
        "• Вы приобретаете цифровой пакет практик Метротерапии.\n"
        "• В Telegram цифровые пакеты оплачиваются только звёздами Telegram (XTR).\n"
        "• Сначала можно пополнить баланс Stars, а затем отдельно подтвердить покупку Метротерапии.\n"
        "• Одна Telegram Star не равна одному рублю: стоимость Stars в обычной валюте "
        "определяет Telegram и она может отличаться в зависимости от страны и способа покупки.\n"
        "• Количество практик, состав пакета и цена показываются до подтверждения платежа.\n"
        "• Stars списываются только после подтверждения в окне Telegram.\n"
        "• Практики начисляются только после подтверждения платежа Telegram.\n"
        "• Повторное подтверждение одного платежа не приводит к повторному начислению.\n"
        "• По вопросам оплаты и возврата используйте /paysupport.\n"
        f"• Поддержка: {support}.\n"
        f"{terms_line}\n"
        "Продолжая оплату, Вы подтверждаете, что прочитали и принимаете условия."
    )


def payment_terms_html() -> str:
    merchant = html.escape(payment_merchant_name())
    support = html.escape(payment_support_contact())
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Условия оплаты — Метротерапия</title>
  <style>
    body {{ max-width: 760px; margin: 0 auto; padding: 32px 20px; font: 16px/1.55 system-ui, sans-serif; color: #172033; }}
    h1, h2 {{ line-height: 1.2; }}
    .note {{ padding: 14px 16px; border-radius: 12px; background: #f3f6fb; }}
  </style>
</head>
<body>
  <h1>Условия оплаты цифровых пакетов</h1>
  <p>Продавец и оператор сервиса: <strong>{merchant}</strong>.</p>
  <h2>Предмет покупки</h2>
  <p>Пользователь приобретает цифровой пакет практик. Состав пакета и количество практик показываются до оплаты.</p>
  <h2>Оплата и предоставление доступа</h2>
  <p>В Telegram цифровые пакеты оплачиваются звёздами Telegram (XTR). В других поддерживаемых каналах — VK, MAX и web — может использоваться защищённый checkout ЮKassa.</p>
  <p>Покупка Stars и оплата пакета являются двумя отдельными подтверждаемыми действиями. Stars списываются только после подтверждения пользователем в интерфейсе Telegram.</p>
  <p>Одна Star не равна одному рублю: стоимость Stars в обычной валюте определяет Telegram и она может различаться в зависимости от страны, налогов и способа покупки.</p>
  <p>Доступ начисляется только после подтверждения платежа соответствующим провайдером. Повторная доставка одного подтверждения не приводит к повторному начислению.</p>
  <h2>Возврат и поддержка</h2>
  <p>Для проверки платежа или запроса возврата используйте команду <code>/paysupport</code> либо обратитесь в поддержку: <strong>{support}</strong>. Возможность возврата зависит от состояния заказа и уже использованного цифрового доступа.</p>
  <p class="note">Практики сервиса не являются медицинской помощью и не заменяют обращение к врачу или психотерапевту.</p>
</body>
</html>"""


def payment_terms_keyboard(*, package_id: str, as_gift: bool) -> InlineKeyboardMarkup:
    action = "gift" if as_gift else "buy"
    methods_action = "gift_methods" if as_gift else "methods"
    rows = [
        [
            InlineKeyboardButton(
                text="✅ Принимаю условия — открыть оплату",
                callback_data=f"stars:{action}:{package_id}",
            )
        ],
    ]
    url = payment_terms_url()
    if url.startswith(("https://", "http://")):
        rows.append([InlineKeyboardButton(text="📄 Полные условия", url=url)])
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к выбору Stars",
                callback_data=f"pay:{methods_action}:{package_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
