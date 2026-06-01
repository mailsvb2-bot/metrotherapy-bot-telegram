from __future__ import annotations

import json
from typing import Any

from services.messenger.menu_contract import CONTEXT_ACTIONS, MAIN_MENU_ACTIONS, main_menu_commands
from services.messenger.package_payment_ui import extract_labeled_urls

BACK_LABEL = "⬅️ Назад"
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
    return _keyboard([
        [_button("🎧 Получить аудио", "continue", "primary"), _button("✅ Прослушал", "done", "positive")],
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
    """VK weather keyboard aligned with Telegram weather entry surface."""
    return _keyboard([
        [
            _button("🌤 Погода", "weather", "primary"),
            _button("🏙 Изменить город", "weather_city", "secondary"),
        ],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
    ])


def vk_weather_city_keyboard_json() -> str:
    """VK keyboard while waiting for city input."""
    return _keyboard([[_button(BACK_LABEL, MENU_COMMAND, "secondary")]])


def vk_score_scale_keyboard_json() -> str:
    """VK keyboard for mood score scale parity.

    Visible labels match Telegram (+1/+2), while payloads use score:<number> so
    1/2 cannot be mistaken for demo route aliases.
    """
    rows: list[list[dict[str, Any]]] = []
    for row in [
        [-10, -9, -8],
        [-7, -6, -5],
        [-4, -3, -2],
        [-1, 0, 1],
        [2, 3, 4],
        [5, 6, 7],
        [8, 9, 10],
    ]:
        rows.append([
            _button(_score_label(value), f"score:{value}", "primary" if value == 0 else "secondary")
            for value in row
        ])
    rows.append([
        _button("📈 Мой прогресс", "progress", "primary"),
        _button(BACK_LABEL, MENU_COMMAND, "secondary"),
    ])
    return _keyboard(rows)


def vk_progress_keyboard_json() -> str:
    return _keyboard([
        [_button("🎧 Получить аудио", "continue", "primary"), _button("✅ Прослушал", "done", "positive")],
        [_button("🔁 Повторить аудио", "repeat", "secondary"), _button("🧾 История", "history", "secondary")],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
    ])


def vk_settings_keyboard_json() -> str:
    return _keyboard([
        [_button("🌦 Погода в моём городе", "weather", "primary")],
        [_button("⏰ Время и правила отправки", "time", "secondary")],
        [_button("💬 Предпочтительный мессенджер", "settings", "secondary")],
        [_button("📨 Каналы по времени дня", "time", "secondary")],
        [_button("📈 Анализ моего состояния", "progress", "primary")],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
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
    return None
