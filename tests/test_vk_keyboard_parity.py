from __future__ import annotations

import json

from runtime.messenger_senders import VkBotSender
from runtime.messenger_vk_sender import _callback_keyboard_json

VK_MAX_BUTTONS_PER_ROW = 5
VK_MAX_BUTTON_ROWS = 6


def _vk_keyboard_with_legacy_extra_controls() -> str:
    return json.dumps(
        {
            "one_time": False,
            "inline": False,
            "buttons": [
                [
                    {"action": {"type": "text", "label": "🌿 Попробовать бесплатно", "payload": json.dumps({"command": "demo"})}, "color": "positive"},
                    {"action": {"type": "text", "label": "🔐 Полный маршрут", "payload": json.dumps({"command": "full"})}, "color": "primary"},
                ],
                [
                    {"action": {"type": "text", "label": "💳 Тарифы", "payload": json.dumps({"command": "pay"})}, "color": "primary"},
                    {"action": {"type": "text", "label": "🎁 Подарить", "payload": json.dumps({"command": "gift"})}, "color": "secondary"},
                ],
                [
                    {"action": {"type": "text", "label": "📈 Мой прогресс", "payload": json.dumps({"command": "progress"})}, "color": "primary"},
                    {"action": {"type": "text", "label": "🧠 Настройки", "payload": json.dumps({"command": "settings"})}, "color": "secondary"},
                ],
                [
                    {"action": {"type": "text", "label": "📣 Посоветовать", "payload": json.dumps({"command": "share"})}, "color": "secondary"},
                    {"action": {"type": "text", "label": "🌤 Погода", "payload": json.dumps({"command": "weather"})}, "color": "secondary"},
                ],
                [
                    {"action": {"type": "text", "label": "🎧 Получить аудио", "payload": json.dumps({"command": "continue"})}, "color": "secondary"},
                    {"action": {"type": "text", "label": "✅ Прослушал", "payload": json.dumps({"command": "done"})}, "color": "positive"},
                ],
            ],
        },
        ensure_ascii=False,
    )


def _wire_keyboard(keyboard_json: str) -> str:
    return _callback_keyboard_json(keyboard_json)


def _commands(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    commands: list[str] = []
    for row in keyboard["buttons"]:
        for button in row:
            payload = json.loads(button["action"].get("payload") or "{}")
            command = payload.get("command")
            if command:
                commands.append(str(command))
    return commands


def _action_types(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    return [button["action"]["type"] for row in keyboard["buttons"] for button in row]


def _assert_vk_limits(keyboard_json: str) -> None:
    keyboard = json.loads(keyboard_json)
    rows = keyboard["buttons"]
    assert len(rows) <= VK_MAX_BUTTON_ROWS
    assert all(len(row) <= VK_MAX_BUTTONS_PER_ROW for row in rows)


def test_vk_main_keyboard_is_telegram_main_parity_without_legacy_controls():
    normalized = VkBotSender._telegram_main_parity_keyboard_json(_vk_keyboard_with_legacy_extra_controls())
    wire = _wire_keyboard(normalized)

    assert _commands(wire) == [
        "demo",
        "full",
        "pay",
        "gift",
        "progress",
        "settings",
        "share",
        "weather",
    ]
    assert set(_action_types(wire)) == {"callback"}
    assert json.loads(wire)["inline"] is True
    _assert_vk_limits(wire)


def test_vk_context_keyboard_is_not_normalized_as_main_menu():
    contextual = json.dumps(
        {
            "one_time": False,
            "inline": False,
            "buttons": [
                [
                    {"action": {"type": "text", "label": "🎧 Получить аудио", "payload": json.dumps({"command": "continue"})}, "color": "secondary"},
                    {"action": {"type": "text", "label": "✅ Прослушал", "payload": json.dumps({"command": "done"})}, "color": "positive"},
                ]
            ],
        },
        ensure_ascii=False,
    )

    assert VkBotSender._telegram_main_parity_keyboard_json(contextual) == contextual
    wire = _wire_keyboard(contextual)
    assert _commands(wire) == ["continue", "done"]
    assert set(_action_types(wire)) == {"callback"}
    assert json.loads(wire)["inline"] is True
    _assert_vk_limits(wire)


def test_vk_full_route_branch_gets_contextual_continue_done_keyboard():
    prepared = VkBotSender._prepare_vk_keyboard_json(
        _vk_keyboard_with_legacy_extra_controls(),
        external_user_id="12345",
        text="🔐 Полный маршрут\n\nНажмите «🎧 Получить аудио».",
    )
    wire = _wire_keyboard(prepared)

    assert _commands(wire) == ["continue", "done", "start"]
    assert set(_action_types(wire)) == {"callback"}
    assert json.loads(wire)["inline"] is True
    _assert_vk_limits(wire)


def test_vk_start_menu_keeps_only_callback_buttons_supported_by_message_event():
    prepared = VkBotSender._prepare_vk_keyboard_json(
        _vk_keyboard_with_legacy_extra_controls(),
        external_user_id="12345",
        text="Главное меню",
    )
    wire = _wire_keyboard(prepared)

    assert _commands(wire) == [
        "demo",
        "full",
        "pay",
        "gift",
        "progress",
        "settings",
        "share",
        "weather",
    ]
    assert set(_action_types(wire)) == {"callback"}
    assert json.loads(wire)["inline"] is True
    _assert_vk_limits(wire)
