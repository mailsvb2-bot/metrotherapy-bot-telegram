from __future__ import annotations

import json

from runtime.messenger_senders import VkBotSender


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


def _commands(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    commands: list[str] = []
    for row in keyboard["buttons"]:
        for button in row:
            payload = json.loads(button["action"]["payload"])
            commands.append(payload["command"])
    return commands


def test_vk_main_keyboard_is_telegram_main_parity_without_legacy_controls():
    normalized = VkBotSender._telegram_main_parity_keyboard_json(_vk_keyboard_with_legacy_extra_controls())

    assert _commands(normalized) == [
        "demo",
        "full",
        "pay",
        "gift",
        "progress",
        "settings",
        "share",
        "weather",
    ]


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
