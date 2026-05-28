from __future__ import annotations

import os

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config.settings import settings
from services.plans import get_active_plans
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


def _public_base_url() -> str:
    return (
        os.getenv("MESSENGER_PUBLIC_BASE_URL", "").strip()
        or os.getenv("PAYMENT_PUBLIC_BASE_URL", "").strip()
        or os.getenv("PUBLIC_BASE_URL", "").strip()
        or (settings.MESSENGER_PUBLIC_BASE_URL or "").strip()
        or (settings.TELEGRAM_WEBHOOK_PUBLIC_BASE_URL or "").strip()
    ).rstrip("/")


def _price_label(price_rub: int) -> str:
    return f"{int(price_rub):,} ₽".replace(",", " ")


def _practice_package_rows(*, user_id: int | None, platform: str = "telegram") -> list[list[InlineKeyboardButton]]:
    base_url = _public_base_url()
    rows: list[list[InlineKeyboardButton]] = []
    for package in public_practice_packages():
        label = f"{package.title} — {_price_label(package.price_rub)}"
        if base_url:
            rows.append([
                InlineKeyboardButton(
                    text=label,
                    url=payment_url(
                        base_url,
                        user_id=int(user_id or 0),
                        platform=platform,
                        external_user_id=str(user_id) if user_id else None,
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
    """Public tariff surface.

    The public app UI must show the canonical practice package ladder, not the
    legacy six-row `plans` subscription table (`morning_5`, `both_20`, etc.).
    Payment is routed through the public YooKassa package endpoint so the same
    token/premium reconciliation path is used across Telegram/VK/MAX.
    """
    rows = _practice_package_rows(user_id=user_id, platform="telegram")
    rows.append([InlineKeyboardButton(text="🎁 Подарить подписку другу", callback_data="gift:menu")])
    rows.append([InlineKeyboardButton(text="📣 Посоветовать Метротерапию", callback_data="share:menu")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return kb(rows)


def kb_gift_tariffs(back_cb: str = "gift:menu") -> InlineKeyboardMarkup:
    # Gift invoices are still backed by the legacy Telegram invoice flow, but
    # the visible package ladder must not expose the old 6-row subscription set.
    # Until gift-specific package reconciliation is implemented, reuse the
    # canonical package labels and keep the existing back navigation.
    rows = _practice_package_rows(user_id=None, platform="telegram")
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)])
    return kb(rows)


def kb_legacy_db_tariffs(user_id: int | None = None) -> InlineKeyboardMarkup:
    """Legacy DB tariff keyboard kept for admin/debug compatibility only."""
    plans = get_active_plans()

    rows: list[list[InlineKeyboardButton]] = []
    for p in plans:
        title = p.get("title") or f"{p.get('scope')}:{p.get('days')}"
        price = int(p.get("price") or 0)
        label = f"{title} — {price} ₽"
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"sub:buy:{int(p['id'])}:{price}",
            )
        ])

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
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
