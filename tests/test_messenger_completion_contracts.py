from __future__ import annotations

import json
from pathlib import Path

from runtime import messenger_max_ui
from runtime.messenger_vk_ui import vk_main_keyboard_json, vk_payment_keyboard_json
from services.gift_claims import is_gift_token, normalize_gift_token
from services.messenger.menu_contract import main_menu_commands


def test_telegram_start_handler_imports_gift_claim_contract():
    source = Path("handlers/start.py").read_text(encoding="utf-8")

    assert "claim_gift_token" in source
    assert "is_gift_token" in source
    assert "normalize_gift_token" in source
    assert "@router.message(CommandStart())" in source
    assert "claim gift_" not in source.lower() or "claim_gift_text" in source


def test_gift_token_normalization_supports_telegram_claim_text():
    token = "gift_" + "b" * 32

    assert normalize_gift_token(f"claim {token}") == token
    assert normalize_gift_token(f"/start {token}") == token
    assert is_gift_token(normalize_gift_token(f"claim {token}"))


def test_vk_payment_keyboard_uses_open_link_buttons():
    text = """
💳 Тарифы Метротерапии

Старт — 1 900 ₽
https://bot.example/pay/yookassa?kind=tokens&package_id=practice_start_7

60 практик — 7 900 ₽
https://bot.example/pay/yookassa?kind=tokens&package_id=practice_60
""".strip()

    raw = vk_payment_keyboard_json(text)
    assert raw is not None
    keyboard = json.loads(raw)

    assert keyboard["inline"] is True
    buttons = keyboard["buttons"]
    assert buttons[0][0]["action"]["type"] == "open_link"
    assert buttons[0][0]["action"]["link"].startswith("https://bot.example/pay/yookassa")
    assert buttons[-1][0]["action"]["type"] == "text"


def _vk_commands(raw: str) -> set[str]:
    keyboard = json.loads(raw)
    commands: set[str] = set()
    for row in keyboard["buttons"]:
        for button in row:
            action = button["action"]
            payload = json.loads(action.get("payload") or "{}")
            command = payload.get("command")
            if command:
                commands.add(command)
    return commands


def _max_commands(attachment: dict) -> set[str]:
    commands: set[str] = set()
    for row in attachment["payload"]["buttons"]:
        for button in row:
            payload = button.get("payload") or {}
            command = payload.get("command")
            if command:
                commands.add(command)
    return commands


def test_vk_and_max_main_menus_match_user_contract_without_admin_panel():
    expected = set(main_menu_commands())

    vk_commands = _vk_commands(vk_main_keyboard_json(user_id=123456789))
    max_commands = _max_commands(messenger_max_ui.main_menu_attachment())

    assert expected.issubset(vk_commands)
    assert expected.issubset(max_commands)
    assert "admin" not in vk_commands
    assert "admin" not in max_commands


def test_max_payment_and_gift_texts_get_link_buttons():
    payment = "💳 Тарифы\n\nСтарт — 1 900 ₽\nhttps://bot.example/pay/yookassa?kind=tokens"
    gift = "🎁 Подарить\n\nСтарт — 1 900 ₽\nhttps://bot.example/pay/yookassa?kind=tokens&gift=1"

    payment_buttons = messenger_max_ui.native_keyboard_attachments(payment)
    gift_buttons = messenger_max_ui.native_keyboard_attachments(gift)

    assert payment_buttons[0]["payload"]["buttons"][0][0]["type"] == "link"
    assert gift_buttons[0]["payload"]["buttons"][0][0]["type"] == "link"


def test_reply_dispatcher_has_no_embedded_max_upload_transport():
    source = Path("services/messenger/reply_dispatcher.py").read_text(encoding="utf-8")

    assert "platform-api.max.ru/uploads" not in source
    assert "multipart_upload" not in source
    assert "send_image_file" in Path("runtime/messenger_max_sender.py").read_text(encoding="utf-8")
