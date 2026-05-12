from __future__ import annotations

import json
from typing import Any

from config.settings import ADMIN_IDS
from services.messenger.menu_contract import CONTEXT_ACTIONS, MAIN_MENU_ACTIONS, main_menu_commands


def _button(label: str, command: str, color: str = "secondary") -> dict[str, Any]:
    return {
        "action": {
            "type": "text",
            "label": label,
            "payload": json.dumps({"command": command}, ensure_ascii=False),
        },
        "color": color,
    }


def _keyboard(rows: list[list[dict[str, Any]]]) -> str:
    return json.dumps(
        {"one_time": False, "inline": False, "buttons": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _is_admin(user_id: int | None) -> bool:
    try:
        return user_id is not None and int(user_id) in {int(item) for item in ADMIN_IDS}
    except (TypeError, ValueError):
        return False


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
    label_aliases["⬅️ меню"] = "start"
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
        [_button("⬅️ Меню", "start", "secondary")],
    ])


def prepare_vk_keyboard_json(keyboard_json: str, *, external_user_id: str, text: str) -> str:
    if (text or "").lstrip().startswith("🔐 Полный маршрут"):
        return full_route_keyboard_json()
    return telegram_main_parity_keyboard_json(keyboard_json)


def vk_main_keyboard_json(user_id: int | None = None) -> str:
    """Persistent VK keyboard aligned with Telegram ``kb_main``.

    The main menu is rendered from the canonical cross-messenger contract, not
    from a handwritten duplicate. Context-only controls such as “Получить аудио”
    and “Прослушал” are intentionally excluded from the main menu and are shown
    only in route/audio contexts.
    """
    rows: list[list[dict[str, Any]]] = []
    actions = list(MAIN_MENU_ACTIONS)
    for idx in range(0, len(actions), 2):
        rows.append([
            _button(action.title, action.command, action.vk_color)
            for action in actions[idx:idx + 2]
        ])
    if _is_admin(user_id):
        rows.append([_button("🛠 Панель", "admin", "primary")])
    return _keyboard(rows)


def vk_default_keyboard_json() -> str:
    """Backward-compatible alias for the canonical VK main menu."""
    return vk_main_keyboard_json()


def vk_demo_kind_keyboard_json() -> str:
    """VK keyboard for Telegram demo-kind parity."""
    return _keyboard([
        [_button("🚗 Практика на утро / дорогу", "demo_work", "positive")],
        [_button("🌙 Практика на вечер / домой", "demo_home", "primary")],
        [_button("⬅️ Назад", "start", "secondary")],
    ])


def vk_weather_keyboard_json() -> str:
    """VK weather keyboard aligned with Telegram weather entry surface."""
    return _keyboard([
        [
            _button("🔄 Обновить погоду", "weather", "primary"),
            _button("🏙 Изменить город", "weather_city", "secondary"),
        ],
        [_button("⬅️ Меню", "start", "secondary")],
    ])


def vk_weather_city_keyboard_json() -> str:
    """VK keyboard while waiting for city input."""
    return _keyboard([[_button("⬅️ Меню", "start", "secondary")]])


def vk_score_scale_keyboard_json() -> str:
    """VK keyboard for mood score scale parity."""
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
            _button(("+" if value > 0 else "") + str(value), str(value), "primary" if value == 0 else "secondary")
            for value in row
        ])
    rows.append([
        _button("📊 Прогресс", "progress", "primary"),
        _button("⬅️ Меню", "start", "secondary"),
    ])
    return _keyboard(rows)


def vk_text_send_kwargs(platform: str, text: str = "", *, user_id: int | None = None) -> dict[str, Any]:
    if platform != "vk":
        return {}
    return {"keyboard_json": vk_main_keyboard_json(user_id)}


def with_vk_keyboard(platform: str, kwargs: dict[str, Any], *, user_id: int | None = None) -> dict[str, Any]:
    if platform != "vk":
        return kwargs
    enriched = dict(kwargs)
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
    return None
