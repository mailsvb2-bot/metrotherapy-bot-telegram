from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from services.gift_claims import create_gift_checkout_token
from services.payments.checkout_intent import add_checkout_intent_to_url
from services.payments.public_url import payment_public_base_url
from services.practice_token_contract import (
    package_by_id,
    public_practice_packages,
    telegram_stars_enabled,
    telegram_stars_price,
    telegram_yookassa_enabled,
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
    return f"{int(price_xtr):,} звёзд".replace(",", " ")


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


def _telegram_package_rows(*, gift: bool) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    action = "gift_methods" if gift else "methods"
    for package in public_practice_packages():
        stars = _stars_label(telegram_stars_price(package.package_id))
        rows.append([
            InlineKeyboardButton(
                text=f"📦 {package.title}: {stars} или {_price_label(package.price_rub)}",
                callback_data=f"pay:{action}:{package.package_id}",
            )
        ])
    return rows


def telegram_payment_method_text(package_id: str) -> str:
    package = package_by_id(package_id)
    if not package.public:
        raise ValueError("payment_package_not_public")
    yookassa = (
        f"💳 Банковской картой через ЮKassa — {_price_label(package.price_rub)}\n"
        "Откроется защищённая страница оплаты во внешнем браузере."
        if telegram_yookassa_enabled()
        else "💳 Оплата банковской картой через ЮKassa временно недоступна."
    )
    stars = (
        f"⭐ Звёздами Telegram — {_stars_label(telegram_stars_price(package.package_id))}\n"
        "Нативная оплата внутри Telegram."
        if telegram_stars_enabled()
        else "⭐ Оплата звёздами Telegram временно недоступна."
    )
    return (
        f"{package.title}\n"
        f"{package.description}\n\n"
        "Выберите способ оплаты:\n\n"
        f"{stars}\n\n"
        f"{yookassa}"
    )


def kb_telegram_payment_methods(
    *,
    user_id: int,
    package_id: str,
    gift: bool = False,
) -> InlineKeyboardMarkup:
    package = package_by_id(package_id)
    if not package.public:
        raise ValueError("payment_package_not_public")
    buyer_id = int(user_id)
    if buyer_id <= 0:
        raise ValueError("payment_buyer_required")

    rows: list[list[InlineKeyboardButton]] = []
    if telegram_stars_enabled():
        stars_action = "gift_terms" if gift else "terms"
        rows.append([
            InlineKeyboardButton(
                text=f"⭐ Звёздами Telegram — {_stars_label(telegram_stars_price(package.package_id))}",
                callback_data=f"stars:{stars_action}:{package.package_id}",
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                text="⭐ Оплата звёздами временно недоступна",
                callback_data="tariffs:stars_disabled",
            )
        ])

    base_url = payment_public_base_url()
    yookassa_label = f"💳 Картой через ЮKassa — {_price_label(package.price_rub)}"
    if not telegram_yookassa_enabled():
        rows.append([
            InlineKeyboardButton(
                text="💳 Оплата через ЮKassa временно недоступна",
                callback_data="tariffs:yookassa_disabled",
            )
        ])
    elif not base_url:
        rows.append([
            InlineKeyboardButton(
                text=yookassa_label,
                callback_data="tariffs:public_base_missing",
            )
        ])
    else:
        gift_token = None
        if gift:
            gift_token = create_gift_checkout_token(
                buyer_user_id=buyer_id,
                package_id=package.package_id,
                source_platform="telegram",
            )
        rows.append([
            InlineKeyboardButton(
                text=yookassa_label,
                url=_practice_payment_url(
                    base_url=base_url,
                    user_id=buyer_id,
                    platform="telegram",
                    package_id=package.package_id,
                    gift_token=gift_token,
                ),
            )
        ])
    rows.append([InlineKeyboardButton(text="📜 Условия оплаты", callback_data="stars:terms")])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="gift:menu" if gift else "sub:menu",
        )
    ])
    return kb(rows)


def kb_telegram_gift_yookassa_checkout(*, user_id: int, package_id: str) -> InlineKeyboardMarkup:
    package = package_by_id(package_id)
    if not package.public:
        raise ValueError("payment_package_not_public")
    buyer_id = int(user_id)
    if buyer_id <= 0:
        raise ValueError("payment_buyer_required")
    if not telegram_yookassa_enabled():
        raise ValueError("telegram_yookassa_disabled")
    base_url = payment_public_base_url()
    if not base_url:
        raise ValueError("payment_public_base_missing")
    gift_token = create_gift_checkout_token(
        buyer_user_id=buyer_id,
        package_id=package.package_id,
        source_platform="telegram",
    )
    url = _practice_payment_url(
        base_url=base_url,
        user_id=buyer_id,
        platform="telegram",
        package_id=package.package_id,
        gift_token=gift_token,
    )
    return kb([
        [InlineKeyboardButton(text=f"💳 Перейти к ЮKassa — {_price_label(package.price_rub)}", url=url)],
        [InlineKeyboardButton(text="⬅️ К способам оплаты", callback_data=f"pay:gift_methods:{package.package_id}")],
    ])


def _external_package_rows(
    *,
    user_id: int | None,
    platform: str,
    gift: bool,
) -> list[list[InlineKeyboardButton]]:
    base_url = payment_public_base_url()
    rows: list[list[InlineKeyboardButton]] = []
    for package in public_practice_packages():
        label = f"💳 Картой через ЮKassa — {package.title}: {_price_label(package.price_rub)}"
        if not base_url:
            rows.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data="tariffs:public_base_missing",
                )
            ])
            continue

        gift_token = None
        if gift:
            gift_token = create_gift_checkout_token(
                buyer_user_id=int(user_id or 0),
                package_id=package.package_id,
                source_platform=platform,
            )
        rows.append([
            InlineKeyboardButton(
                text=label,
                url=_practice_payment_url(
                    base_url=base_url,
                    user_id=user_id,
                    platform=platform,
                    package_id=package.package_id,
                    gift_token=gift_token,
                ),
            )
        ])
    return rows


def _practice_package_rows(
    *,
    user_id: int | None,
    platform: str = "telegram",
    gift: bool = False,
) -> list[list[InlineKeyboardButton]]:
    if gift and not int(user_id or 0):
        return [[
            InlineKeyboardButton(
                text="⚠️ Не удалось определить покупателя подарка",
                callback_data="gift:menu",
            )
        ]]

    if platform == "telegram":
        return _telegram_package_rows(gift=gift)
    return _external_package_rows(user_id=user_id, platform=platform, gift=gift)


def kb_tariffs(user_id: int | None = None) -> InlineKeyboardMarkup:
    """Public Telegram tariff surface with an explicit payment-method step."""
    rows = _practice_package_rows(user_id=user_id, platform="telegram")
    rows.append([InlineKeyboardButton(text="📜 Условия оплаты", callback_data="stars:terms")])
    rows.append([InlineKeyboardButton(text="🎁 Подарить", callback_data="gift:menu")])
    rows.append([InlineKeyboardButton(text="📣 Посоветовать", callback_data="share:menu")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return kb(rows)


def kb_gift_tariffs(user_id: int | None = None, back_cb: str = "gift:menu") -> InlineKeyboardMarkup:
    rows = _practice_package_rows(user_id=user_id, platform="telegram", gift=True)
    rows.append([InlineKeyboardButton(text="📜 Условия оплаты", callback_data="stars:terms")])
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
