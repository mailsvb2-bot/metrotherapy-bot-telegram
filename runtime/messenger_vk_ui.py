from __future__ import annotations

import json
from typing import Any

from services.messenger.menu_contract import CONTEXT_ACTIONS, MAIN_MENU_ACTIONS, main_menu_commands
from services.messenger.package_payment_ui import extract_labeled_urls

BACK_LABEL = "⬅️ Назад"
MENU_LABEL = "⬅️ Меню"
HOME_LABEL = "🏠 Меню"
MAIN_MENU_LABEL = "⬅️ Главное меню"
MENU_COMMAND = "start"


def _button(label: str, command: str, color: str = "secondary") -> dict[str, Any]:
    return {
        "action": {
            "type": "text",
            "label": label,
            "payload": json.dumps({"command": command}, ensure_ascii=False),
        },
        "color": color,
    }


def _open_link_button(label: str, url: str) -> dict[str, Any]:
    return {
        "action": {
            "type": "open_link",
            "label": str(label or "Открыть")[:40],
            "link": str(url or ""),
            "payload": json.dumps({"url": str(url or "")}, ensure_ascii=False),
        }
    }


def _keyboard(rows: list[list[dict[str, Any]]], *, inline: bool = False) -> str:
    return json.dumps(
        {"one_time": False, "inline": inline, "buttons": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _score_label(value: int) -> str:
    return f"{value:+d}" if value != 0 else "0"


def button_command(button: Any) -> str:
    if not isinstance(button, dict):
        return ""
    action = button.get("action") or {}
    payload = action.get("payload")
    if isinstance(payload, str) and payload.strip():
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            command = decoded.get("command") or decoded.get("cmd") or decoded.get("action")
            if isinstance(command, str) and command.strip():
                return command.strip()
    label = str(action.get("label") or "").strip().casefold().replace("ё", "е")
    label_aliases = {action.title.casefold().replace("ё", "е"): action.command for action in MAIN_MENU_ACTIONS + CONTEXT_ACTIONS}
    label_aliases[BACK_LABEL.casefold().replace("ё", "е")] = MENU_COMMAND
    label_aliases[MENU_LABEL.casefold().replace("ё", "е")] = MENU_COMMAND
    label_aliases[HOME_LABEL.casefold().replace("ё", "е")] = MENU_COMMAND
    label_aliases[MAIN_MENU_LABEL.casefold().replace("ё", "е")] = MENU_COMMAND
    label_aliases["⬅️ меню"] = MENU_COMMAND
    label_aliases["⬅️ назад"] = MENU_COMMAND
    label_aliases["назад"] = MENU_COMMAND
    return label_aliases.get(label, "")


def telegram_main_parity_keyboard_json(keyboard_json: str) -> str:
    try:
        keyboard = json.loads(keyboard_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return keyboard_json
    if not isinstance(keyboard, dict):
        return keyboard_json
    rows = keyboard.get("buttons")
    if not isinstance(rows, list):
        return keyboard_json

    all_commands: set[str] = set()
    row_commands: list[tuple[list[Any], set[str]]] = []
    for row in rows:
        if not isinstance(row, list):
            row_commands.append((row, set()))
            continue
        commands = {button_command(button) for button in row}
        commands.discard("")
        all_commands.update(commands)
        row_commands.append((row, commands))

    telegram_main_commands = set(main_menu_commands())
    vk_only_main_controls = {"continue", "done"}
    if not telegram_main_commands.issubset(all_commands):
        return keyboard_json
    if not vk_only_main_controls.intersection(all_commands):
        return keyboard_json

    filtered_rows = [row for row, commands in row_commands if not commands or not commands.issubset(vk_only_main_controls)]
    normalized = dict(keyboard)
    normalized["buttons"] = filtered_rows
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))



def full_route_keyboard_json() -> str:
    # Telegram kb_full_access_menu parity.
    return _keyboard([
        [_button("🔐 Открыть полный маршрут", "pay", "primary")],
        [_button("⏰ Напомнить завтра утром", "remind_continue_tomorrow", "secondary")],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
    ])

def vk_payment_keyboard_json(text: str) -> str | None:
    links = extract_labeled_urls(text)
    if not links:
        return None
    rows: list[list[dict[str, Any]]] = []
    for label, url in links[:4]:
        rows.append([_open_link_button(label, url)])
    rows.append([_button(BACK_LABEL, MENU_COMMAND, "secondary")])
    return _keyboard(rows, inline=True)


def prepare_vk_keyboard_json(keyboard_json: str, *, external_user_id: str, text: str) -> str:
    payment_keyboard = vk_payment_keyboard_json(text)
    if payment_keyboard is not None:
        return payment_keyboard
    if (text or "").lstrip().startswith("🔐 Полный маршрут"):
        return full_route_keyboard_json()
    return telegram_main_parity_keyboard_json(keyboard_json)


def vk_main_keyboard_json(user_id: int | None = None) -> str:
    """Persistent VK user keyboard aligned with Telegram ``kb_main``.

    Admin/control-plane surfaces intentionally stay Telegram-only. VK is a user
    channel; even ADMIN_IDS do not receive admin buttons here.
    """
    _ = user_id
    rows: list[list[dict[str, Any]]] = []
    actions = list(MAIN_MENU_ACTIONS)
    for idx in range(0, len(actions), 2):
        rows.append([
            _button(action.title, action.command, action.vk_color)
            for action in actions[idx:idx + 2]
        ])
    return _keyboard(rows)


def vk_default_keyboard_json() -> str:
    """Backward-compatible alias for the canonical VK main menu."""
    return vk_main_keyboard_json()


def vk_demo_kind_keyboard_json() -> str:
    """VK keyboard for Telegram demo-kind parity."""
    return _keyboard([
        [_button("🚗 Практика на утро / дорогу", "demo_work", "positive")],
        [_button("🌙 Практика на вечер / домой", "demo_home", "primary")],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
    ])



def vk_weather_keyboard_json() -> str:
    """VK weather keyboard aligned with Telegram kb_weather."""
    return _keyboard([
        [_button("🏙 Изменить город", "weather_city", "secondary")],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
    ])

def vk_weather_city_keyboard_json() -> str:
    """VK keyboard while waiting for city input."""
    return _keyboard([[_button(BACK_LABEL, MENU_COMMAND, "secondary")]])



def vk_score_scale_keyboard_json() -> str:
    """VK keyboard for Telegram kb_mood_scale parity."""
    rows: list[list[dict[str, Any]]] = []
    vals = list(range(-10, 11))
    for idx in range(0, len(vals), 7):
        rows.append([
            _button(_score_label(value), str(value), "secondary")
            for value in vals[idx:idx + 7]
        ])
    rows.append([_button(MENU_LABEL, MENU_COMMAND, "secondary")])
    return _keyboard(rows)


def vk_progress_keyboard_json() -> str:
    # Telegram state-period surface parity, not a second audio-control menu.
    return vk_state_period_keyboard_json()

def vk_settings_keyboard_json() -> str:
    return _keyboard([
        [_button("🌦 Погода в моём городе", "weather", "primary")],
        [_button("⏰ Время: дорога на работу", "time", "secondary")],
        [_button("⏰ Время: дорога домой", "time", "secondary")],
        [_button("🎁 Мои бонусы за приглашения", "share", "secondary")],
        [_button("💬 Предпочтительный мессенджер", "settings", "secondary")],
        [_button("📨 Каналы по времени дня", "time", "secondary")],
        [_button("📈 Анализ моего состояния", "progress", "primary")],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
    ])


def vk_delivery_slots_keyboard_json() -> str:
    return _keyboard([
        [_button("🌅 Утренние отправки", "channel morning auto", "primary")],
        [_button("🌙 Вечерние отправки", "channel evening auto", "primary")],
        [_button(BACK_LABEL, "settings", "secondary")],
    ])


def vk_delivery_channel_select_keyboard_json(slot: str = "morning") -> str:
    slot = "evening" if str(slot).strip().lower() == "evening" else "morning"
    return _keyboard([
        [_button("♻️ Авто", f"channel {slot} auto", "secondary")],
        [_button("telegram", f"channel {slot} telegram", "secondary")],
        [_button("max", f"channel {slot} max", "secondary")],
        [_button("vk", f"channel {slot} vk", "secondary")],
        [_button(BACK_LABEL, "time", "secondary")],
    ])


def vk_state_period_keyboard_json() -> str:
    return _keyboard([
        [_button("⭐ Оценить состояние сейчас", "continue", "primary")],
        [_button("📅 Сегодня", "progress", "secondary"), _button("📆 Вчера", "history", "secondary")],
        [_button("🗓 За всё время", "progress", "secondary")],
        [_button("🔐 Открыть полный маршрут", "pay", "primary"), _button("🎁 Подарить", "gift", "secondary")],
        [_button(MENU_LABEL, MENU_COMMAND, "secondary")],
    ])


def vk_post_actions_keyboard_json() -> str:
    return _keyboard([
        [_button("📈 Посмотреть изменение состояния", "progress", "primary")],
        [_button("🔐 Открыть полный маршрут", "full", "primary")],
        [_button("🎧 Ещё одна бесплатная практика", "demo", "positive")],
        [_button("🎁 Подарить подписку", "gift", "secondary")],
        [_button(MAIN_MENU_LABEL, MENU_COMMAND, "secondary")],
    ])


def vk_sales_offer_keyboard_json() -> str:
    return _keyboard([
        [_button("🔐 Открыть полный маршрут", "pay", "primary")],
        [_button("🎧 Ещё одна бесплатная практика", "demo", "positive")],
        [_button("🎁 Подарить подписку другу", "gift", "secondary")],
        [_button(MENU_LABEL, MENU_COMMAND, "secondary")],
    ])



def vk_full_access_keyboard_json() -> str:
    return full_route_keyboard_json()

def vk_settings_locked_keyboard_json() -> str:
    return _keyboard([
        [_button("🔐 Открыть полный маршрут", "pay", "primary")],
        [_button("🎁 Передать ритм", "gift", "secondary"), _button(BACK_LABEL, "settings", "secondary")],
    ])


def vk_ref_bonus_actions_keyboard_json() -> str:
    return _keyboard([
        [_button("🔐 Открыть полный маршрут", "pay", "primary")],
        [_button("🎁 Подарить подписку другу", "gift", "secondary")],
        [_button("📈 Анализ моего состояния", "progress", "primary")],
        [_button(BACK_LABEL, "settings", "secondary")],
    ])


def vk_text_send_kwargs(platform: str, text: str = "", *, user_id: int | None = None) -> dict[str, Any]:
    if platform != "vk":
        return {}
    payment_keyboard = vk_payment_keyboard_json(text)
    if payment_keyboard is not None:
        return {"keyboard_json": payment_keyboard}
    return {"keyboard_json": vk_main_keyboard_json(user_id)}


def with_vk_keyboard(platform: str, kwargs: dict[str, Any], *, user_id: int | None = None) -> dict[str, Any]:
    if platform != "vk":
        return kwargs
    enriched = dict(kwargs)
    text = str(enriched.pop("_text_for_keyboard", "") or "")
    payment_keyboard = vk_payment_keyboard_json(text)
    if payment_keyboard is not None:
        enriched["keyboard_json"] = payment_keyboard
    elif text.lstrip().startswith("🎧 Общий прогресс") or text.lstrip().startswith("🎧 Вы ещё не запускали") or "📈 Мой прогресс" in text or "📈 Анализ состояния" in text:
        enriched.setdefault("keyboard_json", vk_progress_keyboard_json())
    elif text.lstrip().startswith("⚙️ Настройки канала"):
        enriched.setdefault("keyboard_json", vk_settings_keyboard_json())
    elif text.lstrip().startswith("🕒 Правила отправки") or "Каналы по времени дня" in text:
        enriched.setdefault("keyboard_json", vk_delivery_slots_keyboard_json())
    else:
        enriched.setdefault("keyboard_json", vk_main_keyboard_json(user_id))
    return enriched


def keyboard_for_reply_kind(kind: str | None) -> str | None:
    if kind == "demo_kind":
        return vk_demo_kind_keyboard_json()
    if kind == "score_scale":
        return vk_score_scale_keyboard_json()
    if kind == "weather":
        return vk_weather_keyboard_json()
    if kind == "weather_city":
        return vk_weather_city_keyboard_json()
    if kind == "progress":
        return vk_progress_keyboard_json()
    if kind == "settings":
        return vk_settings_keyboard_json()
    if kind == "delivery_slots":
        return vk_delivery_slots_keyboard_json()
    if kind == "delivery_morning":
        return vk_delivery_channel_select_keyboard_json("morning")
    if kind == "delivery_evening":
        return vk_delivery_channel_select_keyboard_json("evening")
    if kind == "state_period":
        return vk_state_period_keyboard_json()
    if kind == "post_actions":
        return vk_post_actions_keyboard_json()
    if kind == "sales_offer":
        return vk_sales_offer_keyboard_json()
    if kind == "full_access":
        return vk_full_access_keyboard_json()
    if kind == "settings_locked":
        return vk_settings_locked_keyboard_json()
    if kind == "ref_bonus":
        return vk_ref_bonus_actions_keyboard_json()
    return None
