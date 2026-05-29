from __future__ import annotations

import json

from runtime.messenger_vk_ui import vk_payment_keyboard_json
from services.gift_claims import is_gift_token, normalize_gift_token


def test_telegram_start_handler_imports_gift_claim_contract():
    source = __import__("pathlib").Path("handlers/start.py").read_text(encoding="utf-8")

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
