from functools import lru_cache
from config.settings import ADMIN_IDS
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from services.roles import ROLE_ADMIN, ROLE_SUPPORT, ROLE_MARKETING
from core.callbacks import ADMIN_TARIFFS


def kb_menu_only() -> InlineKeyboardMarkup:
    """Одна кнопка: 🏠 Меню (возврат в главное меню)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")
    ]])


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


@lru_cache()
def kb_main(user_id: int | None = None):
    rows = [
        [
            InlineKeyboardButton(text="🌿 Попробовать бесплатно", callback_data="demo"),
            InlineKeyboardButton(text="🔐 Полный маршрут", callback_data="full"),
        ],
        [
            InlineKeyboardButton(text="💳 Тарифы", callback_data="sub:menu"),
            InlineKeyboardButton(text="🎁 Подарить", callback_data="gift:menu"),
        ],
        [
            InlineKeyboardButton(text="📈 Мой прогресс", callback_data="settings:state"),
            InlineKeyboardButton(text="🧠 Настройки", callback_data="settings:menu"),
        ],
        [
            InlineKeyboardButton(text="📣 Посоветовать", callback_data="share:menu"),
            InlineKeyboardButton(text="🌤 Погода", callback_data="weather:show"),
        ],
    ]
    is_admin = bool(user_id) and (int(user_id) in set(ADMIN_IDS))
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Панель", callback_data="admin:menu")])

    return _kb(rows)


@lru_cache()
def kb_state_after_charts():
    return _kb([
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:menu")],
    ])


def kb_staff_menu(
    roles: set[str] | None = None,
    *,
    is_superadmin: bool = False,
    allowed_perms: set[str] | None = None,
):
    roles = roles or set()

    rows = []

    def _allow(perm: str) -> bool:
        if is_superadmin:
            return True
        if allowed_perms is None:
            return True
        return perm in allowed_perms

    if ROLE_SUPPORT in roles or ROLE_ADMIN in roles or is_superadmin:
        rows += [
            [InlineKeyboardButton(text="📊 Демо (кратко)", callback_data="admin:demo:brief")],
            [InlineKeyboardButton(text="📈 Демо (подробно)", callback_data="admin:demo:full")],
            [InlineKeyboardButton(text="👥 Пользователи сегодня", callback_data="admin:users:today")],
            [InlineKeyboardButton(text="🔎 Карточка пользователя", callback_data="admin:user:card")],
            [InlineKeyboardButton(text="🧠 Поведение", callback_data="admin:behavior")],
            [InlineKeyboardButton(text="💬 Мессенджеры", callback_data="admin:messenger:overview")],
            [InlineKeyboardButton(text="⚠️ Проверить оплаты", callback_data="admin:payment:problems")],
        ]

    if allowed_perms is not None and not is_superadmin:
        rows = [r for r in rows if _allow(r[0].callback_data)]  # type: ignore[attr-defined]

    if ROLE_MARKETING in roles or ROLE_ADMIN in roles or is_superadmin:
        rows += [
            [InlineKeyboardButton(text="📣 Рекламные ссылки", callback_data="admin:adlinks")],
            [InlineKeyboardButton(text="📉 Путь до оплаты", callback_data="admin:funnel")],
            [InlineKeyboardButton(text="💰 Деньги и клиенты", callback_data="admin:money:today")],
            [InlineKeyboardButton(text="💰 Оплаты", callback_data="admin:conversion")],
            [InlineKeyboardButton(text="🧲 Группы пользователей", callback_data="admin:segments")],
            [InlineKeyboardButton(text="🧪 Проверка предложений", callback_data="admin:ab")],
            [InlineKeyboardButton(text="✍️ Подготовить тексты", callback_data="admin:copy:menu")],
            [InlineKeyboardButton(text="💡 Подсказка по ценам", callback_data="admin:ai:prices")],
        ]

    if allowed_perms is not None and not is_superadmin:
        rows = [r for r in rows if _allow(r[0].callback_data)]  # type: ignore[attr-defined]

    if ROLE_ADMIN in roles or is_superadmin:
        rows += [
            [InlineKeyboardButton(text="🎁 Подарки и рекомендации", callback_data="admin:giftshare")],
            [InlineKeyboardButton(text="🧲 Воронка 2.0", callback_data="admin:funnel2")],
            [InlineKeyboardButton(text="🧩 Удержание", callback_data="admin:retention")],
            [InlineKeyboardButton(text="🧾 Мои состояния (10)", callback_data="admin:state:last")],
            [InlineKeyboardButton(text="🧪 Системные проверки", callback_data="admin:system:checks")],
        ]
        if is_superadmin:
            rows.append([InlineKeyboardButton(text="💳 Тарифы", callback_data=ADMIN_TARIFFS)])
            rows.append([InlineKeyboardButton(text="👥 Добавить администратора", callback_data="admin:add_admin")])
            rows.append([InlineKeyboardButton(text="👥 Роли команды", callback_data="admin:roles:menu")])
            rows.append([InlineKeyboardButton(text="🔐 Доступы админов", callback_data="admin:perms")])

    if allowed_perms is not None and not is_superadmin:
        rows = [r for r in rows if _allow(r[0].callback_data)]  # type: ignore[attr-defined]

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return _kb(rows)


def kb_admin_ad_links() -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="➕ Telegram Ads", callback_data="admin:adlinks:create:telegram_ads")],
        [InlineKeyboardButton(text="➕ Пост в Telegram", callback_data="admin:adlinks:create:telegram_post")],
        [InlineKeyboardButton(text="➕ Партнёр/посев", callback_data="admin:adlinks:create:partner")],
        [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")],
    ])


def kb_admin_money_periods(active: str = "today") -> InlineKeyboardMarkup:
    labels = [
        ("today", "Сегодня"),
        ("week", "7 дней"),
        ("month", "30 дней"),
        ("all", "Всё время"),
    ]
    row = []
    for key, title in labels:
        prefix = "✅ " if key == active else ""
        row.append(InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"admin:money:{key}"))
    return _kb([
        row[:2],
        row[2:],
        [InlineKeyboardButton(text="⚠️ Проблемные оплаты", callback_data="admin:payment:problems")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")],
    ])


def kb_admin_money_payments(payment_ids: list[int], active: str = "today") -> InlineKeyboardMarkup:
    rows = []
    for pid in payment_ids[:10]:
        rows.append([InlineKeyboardButton(text=f"Открыть оплату #{int(pid)}", callback_data=f"admin:money:payment:{int(pid)}")])
    rows.extend(kb_admin_money_periods(active).inline_keyboard)
    return _kb(rows)


def kb_admin_money_card(payment_id: int) -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="⬅️ К оплатам за сегодня", callback_data="admin:money:today")],
        [InlineKeyboardButton(text="⚠️ Проверить оплаты", callback_data="admin:payment:problems")],
        [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")],
    ])


@lru_cache()
def kb_admin_menu():
    return kb_staff_menu({ROLE_ADMIN}, is_superadmin=True)

@lru_cache()
def kb_back():
    return _kb([
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]
    ])


@lru_cache()
def kb_back_main():
    return _kb([
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")]
    ])


@lru_cache()
def kb_weather():
    return _kb([
        [InlineKeyboardButton(text="🏙 Изменить город", callback_data="weather:city")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])


@lru_cache()
def kb_demo_kind():
    return _kb([
        [InlineKeyboardButton(text="🚗 Практика на утро / дорогу", callback_data="demo_kind_work")],
        [InlineKeyboardButton(text="🌙 Практика на вечер / домой", callback_data="demo_kind_home")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])
def kb_sub(prices: dict[str, int]):
    def btn(title: str, cb: str):
        price = prices.get(title)
        tail = f"{price} ₽" if price is not None else "? ₽"
        return InlineKeyboardButton(text=f"{title} — {tail}", callback_data=cb)

    return _kb([
        [btn("Утро — 5 дней", "sub:morning:5")],
        [btn("Утро — 20 дней", "sub:morning:20")],
        [btn("Вечер — 5 дней", "sub:evening:5")],
        [btn("Вечер — 20 дней", "sub:evening:20")],
        [btn("Утро+Вечер — 5 дней", "sub:both:5")],
        [btn("Утро+Вечер — 20 дней", "sub:both:20")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])

@lru_cache()
def kb_pay():
    return _kb([
        [InlineKeyboardButton(text="💳 Оплатить выбранный тариф", callback_data="pay:selected")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


@lru_cache()
def kb_sales_offer(user_id: int):
    return _kb([
        [InlineKeyboardButton(text="🔐 Открыть полный маршрут", callback_data="sub:menu")],
        [InlineKeyboardButton(text="🎧 Ещё одна бесплатная практика", callback_data="demo")],
        [InlineKeyboardButton(text="🎁 Подарить подписку другу", callback_data="gift:menu")],
        [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:main")],
    ])


@lru_cache()
def kb_full_access_menu():
    return _kb([
        [InlineKeyboardButton(text="🔐 Открыть полный маршрут", callback_data="sub:menu")],
        [InlineKeyboardButton(text="⏰ Напомнить завтра утром", callback_data="remind:continue_tomorrow")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


@lru_cache()
def kb_settings_locked():
    return _kb([
        [InlineKeyboardButton(text="🔐 Открыть полный маршрут", callback_data="sub:menu")],
        [
            InlineKeyboardButton(text="🎁 Передать ритм", callback_data="gift:menu"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:menu"),
        ],
    ])


@lru_cache()
def kb_sub_extras():
    return _kb([
        [InlineKeyboardButton(text="🎁 Подарить подписку другу", callback_data="gift:menu")],
        [InlineKeyboardButton(text="📣 Посоветовать Метротерапию", callback_data="share:menu")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


def kb_micro_question(q_key: str, options: list[str]):
    rows = []
    for idx, opt in enumerate(options):
        rows.append([InlineKeyboardButton(text=str(opt), callback_data=f"micro:{q_key}:{idx}")])
    rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:main")])
    return _kb(rows)


@lru_cache()
def kb_settings_menu():
    return _kb([
        [InlineKeyboardButton(text="🌦 Погода в моём городе", callback_data="weather:show")],
        [InlineKeyboardButton(text="⏰ Время: дорога на работу", callback_data="settings:time:work")],
        [InlineKeyboardButton(text="⏰ Время: дорога домой", callback_data="settings:time:home")],
        [InlineKeyboardButton(text="🎁 Мои бонусы за приглашения", callback_data="settings:ref")],
        [InlineKeyboardButton(text="💬 Предпочтительный мессенджер", callback_data="settings:platform:menu")],
        [InlineKeyboardButton(text="📨 Каналы по времени дня", callback_data="settings:delivery:channels")],
        [InlineKeyboardButton(text="📈 Анализ моего состояния", callback_data="settings:state")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])


@lru_cache()
def kb_mood_done(session_id: int):
    sid = int(session_id)
    return _kb([
        [InlineKeyboardButton(text="✅ Прослушал", callback_data=f"mood:done:{sid}")],
        [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:main")],
    ])


def kb_body_question(session_id: int, q_key: str, question: str, options: list[str]):
    sid = int(session_id)
    rows = []
    for idx, opt in enumerate(options or []):
        rows.append([InlineKeyboardButton(text=str(opt), callback_data=f"body:{sid}:{q_key}:{idx}")])
    rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:main")])
    return question, _kb(rows)


def kb_messenger_platforms(snapshot: dict, available_targets: list[dict[str, str]]):
    from services.messenger.platforms import MessengerPlatform, platform_title, normalize_platform

    identities = {normalize_platform(item.get('platform')) for item in (snapshot.get('identities') or [])}
    preferred = normalize_platform(snapshot.get('preferred_platform'))
    available = {normalize_platform(item.get('platform')) for item in (available_targets or [])}
    rows = []
    for platform in [MessengerPlatform.TELEGRAM.value, MessengerPlatform.MAX.value, MessengerPlatform.VK.value]:
        title = platform_title(platform)
        prefix = '✅ ' if platform == preferred else ''
        if platform in identities:
            suffix = ' • подключён'
        elif platform in available:
            suffix = ' • ссылка для входа'
        else:
            suffix = ' • не настроен'
        rows.append([InlineKeyboardButton(text=f"{prefix}{title}{suffix}", callback_data=f"settings:platform:set:{platform}")])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='settings:menu')])
    return _kb(rows)


@lru_cache()
def kb_after_post_actions():
    return _kb([
        [InlineKeyboardButton(text="📈 Посмотреть изменение состояния", callback_data="settings:state")],
        [InlineKeyboardButton(text="🔐 Открыть полный маршрут", callback_data="sub:menu")],
        [InlineKeyboardButton(text="🎧 Другая практика", callback_data="demo")],
        [InlineKeyboardButton(text="🎁 Подарить подписку", callback_data="gift:menu")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
    ])


@lru_cache()
def kb_ref_bonus_actions():
    return _kb([
        [InlineKeyboardButton(text="🔐 Открыть полный маршрут", callback_data="sub:menu")],
        [InlineKeyboardButton(text="🎁 Подарить подписку другу", callback_data="gift:menu")],
        [InlineKeyboardButton(text="📈 Анализ моего состояния", callback_data="settings:state")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:menu")],
    ])

@lru_cache()
def kb_mood_scale(session_id: int, *, stage: str):
    stage = (stage or "pre").strip()
    sid = int(session_id)
    vals = list(range(-10, 11))
    rows = []
    for i in range(0, len(vals), 7):
        row = []
        for v in vals[i:i+7]:
            txt = f"{v:+d}" if v != 0 else "0"
            row.append(InlineKeyboardButton(text=txt, callback_data=f"mood:{stage}:{sid}:{v}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:main")])
    return _kb(rows)


@lru_cache()
def kb_post_show_chart(session_id: int):
    return _kb([
        [InlineKeyboardButton(text="📈 Посмотреть график изменения моего состояния", callback_data=f"post:chart:{int(session_id)}")],
        [InlineKeyboardButton(text="🎧 Другая практика", callback_data="demo")],
        [InlineKeyboardButton(text="🔐 Открыть полный маршрут", callback_data="sub:menu")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])


def kb_state_period_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ Оценить состояние сейчас", callback_data="state:rate"),
        ],
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="state:today"),
            InlineKeyboardButton(text="📆 Вчера", callback_data="state:yesterday"),
        ],
        [
            InlineKeyboardButton(text="🗓 За всё время", callback_data="state:all"),
        ],
        [
            InlineKeyboardButton(text="🔐 Открыть полный маршрут", callback_data="sub:menu"),
            InlineKeyboardButton(text="🎁 Подарить", callback_data="gift:menu"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:main"),
        ],
    ])
    return kb


def kb_state_rate_scale() -> InlineKeyboardMarkup:
    rows = []
    nums = list(range(1, 11))
    for i in range(0, len(nums), 5):
        row = [InlineKeyboardButton(text=str(n), callback_data=f"state:rate:{n}") for n in nums[i:i+5]]
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:state")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")])
    return _kb(rows)


def kb_delivery_channel_slots(snapshot: dict):
    rows = [
        [InlineKeyboardButton(text=f"🌅 Утренние отправки: {snapshot.get('morning_channel') or 'авто'}", callback_data="settings:delivery:slot:morning")],
        [InlineKeyboardButton(text=f"🌙 Вечерние отправки: {snapshot.get('evening_channel') or 'авто'}", callback_data="settings:delivery:slot:evening")],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='settings:menu')],
    ]
    return _kb(rows)


def kb_delivery_channel_select(slot: str, snapshot: dict):
    identities = {item.get('platform') for item in (snapshot.get('identities') or [])}
    current = snapshot.get('morning_channel') if slot == 'morning' else snapshot.get('evening_channel')
    rows = [[InlineKeyboardButton(text='♻️ Авто', callback_data=f'settings:delivery:slot:set:{slot}:auto')]]
    for platform in ['telegram', 'max', 'vk']:
        prefix = '✅ ' if current == platform else ''
        suffix = ' • подключён' if platform in identities else ' • fallback'
        rows.append([InlineKeyboardButton(text=f"{prefix}{platform}{suffix}", callback_data=f'settings:delivery:slot:set:{slot}:{platform}')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='settings:delivery:channels')])
    return _kb(rows)
