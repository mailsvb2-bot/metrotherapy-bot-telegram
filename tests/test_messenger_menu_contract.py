from __future__ import annotations

import json

from keyboards.inline import kb_main
from runtime.messenger_senders import VkBotSender
from services.messenger.menu_contract import (
    MAIN_MENU_ACTIONS,
    main_menu_commands,
    main_menu_titles,
    max_numbered_menu_text,
    normalize_menu_command,
    telegram_main_callbacks,
)


def _telegram_main_buttons():
    keyboard = kb_main(user_id=None)
    return [button for row in keyboard.inline_keyboard for button in row]


def test_telegram_main_menu_matches_canonical_contract():
    buttons = _telegram_main_buttons()

    assert [button.text for button in buttons] == list(main_menu_titles())
    assert [button.callback_data for button in buttons] == list(telegram_main_callbacks())


def test_vk_main_menu_parity_uses_same_command_vocabulary():
    keyboard_json = json.dumps(
        {
            "one_time": False,
            "inline": False,
            "buttons": [
                [
                    {
                        "action": {
                            "type": "text",
                            "label": action.title,
                            "payload": json.dumps({"command": action.command}, ensure_ascii=False),
                        },
                        "color": action.vk_color,
                    }
                ]
                for action in MAIN_MENU_ACTIONS
            ]
            + [
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "🎧 Получить аудио",
                            "payload": json.dumps({"command": "continue"}, ensure_ascii=False),
                        },
                        "color": "secondary",
                    }
                ]
            ],
        },
        ensure_ascii=False,
    )

    normalized = VkBotSender._telegram_main_parity_keyboard_json(keyboard_json)
    parsed = json.loads(normalized)
    commands = []
    labels = []
    for row in parsed["buttons"]:
        for button in row:
            labels.append(button["action"]["label"])
            commands.append(json.loads(button["action"]["payload"])["command"])

    assert commands == list(main_menu_commands())
    assert labels == list(main_menu_titles())
    assert "continue" not in commands


def test_command_normalizer_accepts_titles_aliases_and_context_commands():
    assert normalize_menu_command("🌿 Попробовать бесплатно") == "demo"
    assert normalize_menu_command("тарифы") == "pay"
    assert normalize_menu_command("оплатить") == "pay"
    assert normalize_menu_command("Подарить") == "gift"
    assert normalize_menu_command("🎧 Получить аудио") == "continue"
    assert normalize_menu_command("прослушал") == "done"


def test_max_numbered_menu_exposes_all_canonical_commands():
    text = max_numbered_menu_text()
    for action in MAIN_MENU_ACTIONS:
        assert action.title in text
        assert f"отправьте: {action.command}" in text
    assert "continue" in text
    assert "done" in text
