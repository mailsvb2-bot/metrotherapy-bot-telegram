from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.gift_claims import create_gift_checkout_token
from services.payments.checkout_intent import add_checkout_intent_to_url
from services.payments.public_url import payment_public_base_url
from services.payments.stars_links import stars_amount_label, stars_topup_url
from services.practice_token_contract import (
    package_by_id,
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
    return stars_amount_label(price_xtr)


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
                text=f"📦 {package.title} — {stars}",
                callback_data=f"pay:{action}:{package.package_id}",
            )
        ])
    return rows


def telegram_payment_method_text(package_id: str) -> str:
    package = package_by_id(package_id)
    if not package.public:
        raise ValueError("payment_package_not_public")
    amount = _stars_label(telegram_stars_price(package.package_id))
    if not telegram_stars_enabled():
        return (
            f"{package.title}\n"
            f"{package.description}\n\n"
            "Оплата Stars сейчас временно недоступна. Попробуйте позже."
        )
    return (
        f"{package.title}\n"
        f"{package.description}\n\n"
        f"Стоимость: {amount}\n\n"
        "Почему оплата через Stars?\n"
        "Telegram требует оплачивать цифровые услуги внутри ботов с помощью Telegram Stars. "
        "Поэтому для оплаты Метротерапии сначала нужно иметь достаточное количество Stars.\n\n"
        f"Если Stars пока нет, сначала купите {amount} в Telegram, затем вернитесь сюда "
        "и оплатите пакет. Если Stars уже есть, покупать их повторно не нужно.\n\n"
        "Покупка Stars — это пополнение баланса Telegram. На этом этапе Метротерапия "
        "не получает оплату за пакет.\n\n"
        "Выберите, что подходит Вам:\n\n"
        "⭐ Stars уже есть — откроем условия и затем счёт на оплату.\n"
        f"➕ Stars пока нет — сначала Telegram предложит купить {amount}, "
        "после чего Вы вернётесь к оплате Метротерапии.\n\n"
        "На этом экране ничего не списывается."
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
        amount_xtr = telegram_stars_price(package.package_id)
        amount = _stars_label(amount_xtr)
        terms_action = "gift_terms" if gift else "terms"
        object_name = "подарок" if gift else "Метротерапию"
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=f"⭐ У меня уже есть Stars — оплатить {object_name}",
                        callback_data=f"stars:{terms_action}:{package.package_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"➕ Сначала купить {amount}",
                        url=stars_topup_url(amount_xtr=amount_xtr, package_id=package.package_id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"✅ Stars куплены — оплатить {object_name}",
                        callback_data=f"stars:{terms_action}:{package.package_id}",
                    )
                ],
            ]
        )
    else:
        rows.append([
            InlineKeyboardButton(
                text="⭐ Оплата Stars временно недоступна",
                callback_data="tariffs:stars_disabled",
            )
        ])

    rows.append([InlineKeyboardButton(text="📜 Условия оплаты", callback_data="stars:terms")])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад к пакетам",
            callback_data="gift:menu" if gift else "sub:menu",
        )
    ])
    return kb(rows)


def kb_telegram_gift_yookassa_checkout(*, user_id: int, package_id: str) -> InlineKeyboardMarkup:
    """Legacy compatibility entrypoint; Telegram package checkout is Stars-only."""

    del user_id, package_id
    raise ValueError("telegram_yookassa_disabled")


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
    """Public Telegram tariff surface; digital packages are Stars-only."""

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
