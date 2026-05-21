from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config.settings import settings
from services.plans import get_active_plans
from services.practice_tokens import get_active_packages, get_wallet, payment_url

try:
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUser
except (ValueError, AttributeError):  # pragma: no cover
    ReplyKeyboardMarkup = KeyboardButton = ReplyKeyboardRemove = KeyboardButtonRequestUser = None  # type: ignore


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back(to: str = "menu:main") -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton(text="⬅️ Назад", callback_data=to)]])


def payment_public_base_url() -> str:
    base = (
        getattr(settings, "PAYMENT_PUBLIC_BASE_URL", "")
        or getattr(settings, "MESSENGER_PUBLIC_BASE_URL", "")
        or "https://metrotherapy-bot.metrotherapy.ru"
    )
    return str(base).strip().rstrip("/")


def practice_packages_text(user_id: int) -> str:
    wallet = get_wallet(int(user_id))
    return (
        "💳 Пакеты практик\n\n"
        f"Ваш баланс: {wallet.available_tokens} практик.\n\n"
        "1 практика = одно аудио с оценкой состояния ДО и ПОСЛЕ.\n"
        "Если аудио не отправилось, практика не списывается.\n\n"
        "Выберите пакет ниже:\n"
        "🌿 5 практик — 990 ₽\n"
        "🔐 20 практик — 3 490 ₽\n"
        "🌅🌙 60 практик — 7 900 ₽\n\n"
        "Ритм выбирается отдельно: только утро, только вечер или утро + вечер."
    )


def kb_practice_packages(user_id: int, *, platform: str = "telegram", external_user_id: str | None = None) -> InlineKeyboardMarkup:
    base_url = payment_public_base_url()
    rows: list[list[InlineKeyboardButton]] = []
    for package in get_active_packages():
        url = payment_url(
            base_url,
            user_id=int(user_id),
            platform=platform,
            external_user_id=external_user_id or str(int(user_id)),
            package_id=package.package_id,
        )
        rows.append([
            InlineKeyboardButton(
                text=f"{package.title} — {package.price_rub:,} ₽".replace(",", " "),
                url=url,
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return kb(rows)


def kb_tariffs(user_id: int | None = None) -> InlineKeyboardMarkup:
    """Legacy tariffs from DB.

    Kept for backward compatibility with older callbacks/admin flows. The public
    user-facing `sub:menu` screen now uses `kb_practice_packages()`.
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