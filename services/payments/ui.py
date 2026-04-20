from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from services.plans import get_active_plans

try:
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUser
except (ValueError, AttributeError):  # pragma: no cover
    ReplyKeyboardMarkup = KeyboardButton = ReplyKeyboardRemove = KeyboardButtonRequestUser = None  # type: ignore


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back(to: str = "menu:main") -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton(text="⬅️ Назад", callback_data=to)]])


def kb_tariffs(user_id: int | None = None) -> InlineKeyboardMarkup:
    """Тарифы (из БД).

    В callback вшивается ожидаемая цена: sub:buy:<plan_id>:<expected_price>
    """
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

    rows.append([InlineKeyboardButton(text="🎁 Подарить подписку другу", callback_data="gift:menu")])
    rows.append([InlineKeyboardButton(text="📣 Посоветовать Метротерапию", callback_data="share:menu")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return kb(rows)


def kb_gift_tariffs(back_cb: str = "gift:menu") -> InlineKeyboardMarkup:
    plans = get_active_plans()
    rows: list[list[InlineKeyboardButton]] = []
    for p in plans:
        title = p.get("title") or f"{p.get('scope')}:{p.get('days')}"
        price = int(p.get("price") or 0)
        label = f"{title} — {price} ₽"
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"gift:buy:{int(p['id'])}:{price}",
            )
        ])
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
