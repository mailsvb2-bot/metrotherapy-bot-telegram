from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime import messenger_max_ui as max_ui
from runtime import messenger_vk_ui as vk_ui


def max_rows(attachment: dict[str, Any]) -> list[list[str]]:
    return [[button["text"] for button in row] for row in attachment["payload"]["buttons"]]


def vk_rows(keyboard_json: str) -> list[list[str]]:
    parsed = json.loads(keyboard_json)
    return [[button["action"]["label"] for button in row] for row in parsed["buttons"]]


MAX_MAIN = max_rows(max_ui.main_menu_attachment())
MAX_FULL = max_rows(max_ui.full_route_attachment())
VK_MAIN = vk_rows(vk_ui.vk_main_keyboard_json(None))
VK_FULL = vk_rows(vk_ui.full_route_keyboard_json())


def assert_rows(name: str, actual: list[list[str]], expected: list[list[str]]) -> None:
    if actual != expected:
        raise AssertionError(f"{name} is not closed to covered surface\nactual={actual!r}\nexpected={expected!r}")


def main() -> None:
    # MAX: all previously uncovered surfaces must collapse to already-covered
    # main/full surfaces. No payment/link native keyboard until parity-covered.
    assert_rows("MAX settings", max_rows(max_ui.settings_attachment()), MAX_MAIN)
    assert_rows("MAX delivery slots", max_rows(max_ui.delivery_slots_attachment()), MAX_MAIN)
    assert_rows("MAX delivery select morning", max_rows(max_ui.delivery_channel_select_attachment("morning")), MAX_MAIN)
    assert_rows("MAX delivery select evening", max_rows(max_ui.delivery_channel_select_attachment("evening")), MAX_MAIN)
    assert_rows("MAX post actions", max_rows(max_ui.post_actions_attachment()), MAX_MAIN)
    assert_rows("MAX sales offer", max_rows(max_ui.sales_offer_attachment()), MAX_MAIN)
    assert_rows("MAX settings locked", max_rows(max_ui.settings_locked_attachment()), MAX_FULL)
    assert_rows("MAX ref bonus", max_rows(max_ui.ref_bonus_actions_attachment()), MAX_MAIN)

    if max_ui.link_action_attachment("💳 Оплатить https://example.com/pay") is not None:
        raise AssertionError("MAX payment link keyboard is not closed")
    if max_ui.link_action_attachment("🎁 Подарок https://example.com/gift") is not None:
        raise AssertionError("MAX gift link keyboard is not closed")

    # VK: same policy.
    assert_rows("VK settings", vk_rows(vk_ui.vk_settings_keyboard_json()), VK_MAIN)
    assert_rows("VK delivery slots", vk_rows(vk_ui.vk_delivery_slots_keyboard_json()), VK_MAIN)
    assert_rows("VK delivery select morning", vk_rows(vk_ui.vk_delivery_channel_select_keyboard_json("morning")), VK_MAIN)
    assert_rows("VK delivery select evening", vk_rows(vk_ui.vk_delivery_channel_select_keyboard_json("evening")), VK_MAIN)
    assert_rows("VK post actions", vk_rows(vk_ui.vk_post_actions_keyboard_json()), VK_MAIN)
    assert_rows("VK sales offer", vk_rows(vk_ui.vk_sales_offer_keyboard_json()), VK_MAIN)
    assert_rows("VK settings locked", vk_rows(vk_ui.vk_settings_locked_keyboard_json()), VK_FULL)
    assert_rows("VK ref bonus", vk_rows(vk_ui.vk_ref_bonus_actions_keyboard_json()), VK_MAIN)

    if vk_ui.vk_payment_keyboard_json("💳 Оплатить https://example.com/pay") is not None:
        raise AssertionError("VK payment link keyboard is not closed")
    if vk_ui.vk_payment_keyboard_json("🎁 Подарок https://example.com/gift") is not None:
        raise AssertionError("VK gift link keyboard is not closed")

    print("✅ uncovered VK/MAX surfaces are closed to covered Telegram-parity surfaces")


if __name__ == "__main__":
    main()
