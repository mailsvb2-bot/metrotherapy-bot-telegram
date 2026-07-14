from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from services.gift_claims import create_gift_checkout_token
from services.payments.checkout_intent import add_checkout_intent_to_url
from services.payments.public_url import payment_public_base_url
from services.practice_token_contract import (
    public_practice_packages,
    telegram_stars_enabled,
    telegram_stars_price,
)
from services.practice_tokens import payment_url

try:
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUser
except (ValueError, AttributeError):  # pragma: no cover
    ReplyKeyboardMarkup = KeyboardButton = ReplyKeyboardRemove = KeyboardButtonRequestUser = None  # type: ignore


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back(to: str = "menu:main") -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton(text="⬅️ Назад", callback_data=to)]])


def _price_label(price_rub: int) -> str:
    return f"{int(price_rub):,} ₽".replace(",", " ")


def _stars_label(price_xtr: int) -> str:
    return f"{int(price_xtr):,} ⭐".replace(",", " ")


def _practice_payment_url(
    *,
    base_url: str,
    user_id: int | None,
    platform: str,
    package_id: str,
    gift_token: str | None = None,
) -> str:
    raw_url = payment_url(
        base_url,
        user_id=int(user_id or 0),
        platform=platform,
        external_user_id=str(user_id) if user_id else None,
        package_id=package_id,
        gift_token=gift_token,
    )
    return add_checkout_intent_to_url(
        raw_url,
        user_id=int(user_id or 0),
        package_id=package_id,
        kind="tokens",
        source=platform,
        gift_token=gift_token,
    )


def _practice_package_rows(
    *,
    user_id: int | None,
    platform: str = "telegram",
    gift: bool = False,
) -> list[list[InlineKeyboardButton]]:
    base_url = payment_public_base_url()
    rows: list[list[InlineKeyboardButton]] = []
    if gift and not int(user_id or 0):
        rows.append([
            InlineKeyboardButton(
                text="⚠️ Не удалось определить покупателя подарка",
                callback_data="gift:menu",
            )
        ])
        return rows

    for package in public_practice_packages():
        if platform == "telegram" and telegram_stars_enabled():
            stars_action = "gift" if gift else "buy"
            rows.append([
                InlineKeyboardButton(
                    text=f"⭐ Stars · {package.title} — {_stars_label(telegram_stars_price(package.package_id))}",
                    callback_data=f"stars:{stars_action}:{package.package_id}",
                )
            ])

        yookassa_label = f"💳 YooKassa · {package.title} — {_price_label(package.price_rub)}"
        if base_url:
            gift_token = None
            if gift:
                gift_token = create_gift_checkout_token(
                    buyer_user_id=int(user_id or 0),
                    package_id=package.package_id,
                    source_platform=platform,
                )
            rows.append([
                InlineKeyboardButton(
                    text=yookassa_label,
                    url=_practice_payment_url(
                        base_url=base_url,
                        user_id=user_id,
                        platform=platform,
                        package_id=package.package_id,
                        gift_token=gift_token,
                    ),
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text=yookassa_label,
                    callback_data="tariffs:public_base_missing",
                )
            ])
    return rows


def kb_tariffs(user_id: int | None = None) -> InlineKeyboardMarkup:
    """Public Telegram tariff surface with Stars and the canonical YooKassa checkout."""
    rows = _practice_package_rows(user_id=user_id, platform="telegram")
    rows.append([InlineKeyboardButton(text="🎁 Подарить", callback_data="gift:menu")])
    rows.append([InlineKeyboardButton(text="📣 Посоветовать", callback_data="share:menu")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return kb(rows)


def kb_gift_tariffs(user_id: int | None = None, back_cb: str = "gift:menu") -> InlineKeyboardMarkup:
    rows = _practice_package_rows(user_id=user_id, platform="telegram", gift=True)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)])
    return kb(rows)


def kb_pay_selected() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton(text="💳 Оплатить", callback_data="pay:selected")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sub:menu")],
    ])


def kb_after_paid() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton(text="⏰ Дорога на работу", callback_data="settings:time:work")],
        [InlineKeyboardButton(text="⏰ Дорога домой", callback_data="settings:time:home")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")],
    ])


def pick_user_keyboard():
    if ReplyKeyboardMarkup is None or KeyboardButton is None:
        return None
    rows = []
    if KeyboardButtonRequestUser is not None:
        rows.append([KeyboardButton(text="👤 Выбрать друга", request_user=KeyboardButtonRequestUser(request_id=2))])
    rows.append([KeyboardButton(text="❌ Отмена")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)
