from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from services.payments.checkout_intent import add_checkout_intent_to_url
from services.payments.public_url import payment_public_base_url
from services.practice_token_contract import public_practice_packages
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


def _practice_payment_url(*, base_url: str, user_id: int | None, platform: str, package_id: str) -> str:
    raw_url = payment_url(
        base_url,
        user_id=int(user_id or 0),
        platform=platform,
        external_user_id=str(user_id) if user_id else None,
        package_id=package_id,
    )
    return add_checkout_intent_to_url(
        raw_url,
        user_id=int(user_id or 0),
        package_id=package_id,
        kind="tokens",
        source=platform,
    )


def _practice_package_rows(*, user_id: int | None, platform: str = "telegram") -> list[list[InlineKeyboardButton]]:
    base_url = payment_public_base_url()
    rows: list[list[InlineKeyboardButton]] = []
    for package in public_practice_packages():
        label = f"{package.title} — {_price_label(package.price_rub)}"
        if base_url:
            rows.append([
                InlineKeyboardButton(
                    text=label,
                    url=_practice_payment_url(
                        base_url=base_url,
                        user_id=user_id,
                        platform=platform,
                        package_id=package.package_id,
                    ),
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data="tariffs:public_base_missing",
                )
            ])
    return rows


def kb_tariffs(user_id: int | None = None) -> InlineKeyboardMarkup:
    """Public tariff surface: canonical 4-package practice ladder only."""
    rows = _practice_package_rows(user_id=user_id, platform="telegram")
    rows.append([InlineKeyboardButton(text="🎁 Подарить", callback_data="gift:menu")])
    rows.append([InlineKeyboardButton(text="📣 Посоветовать", callback_data="share:menu")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return kb(rows)


def kb_gift_tariffs(back_cb: str = "gift:menu") -> InlineKeyboardMarkup:
    rows = _practice_package_rows(user_id=None, platform="telegram")
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
