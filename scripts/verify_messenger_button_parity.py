from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from keyboards.inline import (
    kb_after_post_actions,
    kb_delivery_channel_select,
    kb_delivery_channel_slots,
    kb_demo_kind,
    kb_full_access_menu,
    kb_main,
    kb_mood_done,
    kb_mood_scale,
    kb_ref_bonus_actions,
    kb_sales_offer,
    kb_settings_locked,
    kb_settings_menu,
    kb_state_period_menu,
    kb_weather,
)
from runtime import messenger_max_ui as max_ui
from runtime import messenger_vk_ui as vk_ui


def delivery_snapshot() -> dict[str, Any]:
    return {"identities": [], "morning_channel": None, "evening_channel": None}


NORMALIZE = {
    "demo": "demo",
    "full": "full",
    "sub:menu": "pay",
    "gift:menu": "gift",
    "settings:state": "progress",
    "settings:menu": "settings",
    "share:menu": "share",
    "weather:show": "weather",
    "demo_kind_work": "demo_work",
    "demo_kind_home": "demo_home",
    "menu:main": "start",
    "back": "start",
    "weather:city": "weather_city",
    "remind:continue_tomorrow": "remind_continue_tomorrow",
    "state:rate": "continue",
    "state:today": "progress",
    "state:yesterday": "history",
    "state:all": "progress",
}


def norm_command(command: str) -> str:
    if command.startswith("mood:done"):
        return "done"
    if command.startswith("mood:"):
        parts = command.split(":")
        return f"score:{parts[-1]}" if len(parts) >= 4 else command
    return NORMALIZE.get(command, command)


def tg_rows(markup: Any) -> list[list[tuple[str, str]]]:
    return [
        [(button.text, norm_command(str(button.callback_data or ""))) for button in row]
        for row in markup.inline_keyboard
    ]


def max_rows(attachment: dict[str, Any]) -> list[list[tuple[str, str]]]:
    rows = attachment["payload"]["buttons"]
    return [
        [(button["text"], norm_command(str((button.get("payload") or {}).get("command") or ""))) for button in row]
        for row in rows
    ]


def vk_rows(keyboard_json: str) -> list[list[tuple[str, str]]]:
    rows = json.loads(keyboard_json)["buttons"]
    out: list[list[tuple[str, str]]] = []
    for row in rows:
        result_row: list[tuple[str, str]] = []
        for button in row:
            action = button["action"]
            payload_raw = action.get("payload") or "{}"
            payload = json.loads(payload_raw)
            command = str(payload.get("command") or "")
            if command.lstrip("-").isdigit():
                command = f"score:{command}"
            result_row.append((action["label"], norm_command(command)))
        out.append(result_row)
    return out


def assert_equal(name: str, actual: list[list[tuple[str, str]]], expected: list[list[tuple[str, str]]]) -> None:
    if actual != expected:
        raise AssertionError(f"{name} mismatch\nactual={actual!r}\nexpected={expected!r}")


def check(name: str, tg: Any, max_attachment: dict[str, Any] | None, vk_keyboard: str | None) -> None:
    expected = tg_rows(tg)
    if max_attachment is not None:
        assert_equal(f"MAX {name}", max_rows(max_attachment), expected)
    if vk_keyboard is not None:
        assert_equal(f"VK {name}", vk_rows(vk_keyboard), expected)


def main() -> None:
    snapshot = delivery_snapshot()

    check("main", kb_main(None), max_ui.main_menu_attachment(), vk_ui.vk_main_keyboard_json(None))
    check("demo", kb_demo_kind(), max_ui.demo_kind_attachment(), vk_ui.vk_demo_kind_keyboard_json())
    check("weather", kb_weather(), max_ui.weather_attachment(), vk_ui.vk_weather_keyboard_json())
    check("full access", kb_full_access_menu(), max_ui.full_route_attachment(), vk_ui.full_route_keyboard_json())
    check("mood scale", kb_mood_scale(123, stage="pre"), max_ui.score_scale_attachment(), vk_ui.vk_score_scale_keyboard_json())
    check("mood done", kb_mood_done(123), max_ui.post_audio_attachment(), None)
    check("state period", kb_state_period_menu(), max_ui.state_period_attachment(), vk_ui.vk_state_period_keyboard_json())

    check("settings", kb_settings_menu(), max_ui.settings_attachment(), vk_ui.vk_settings_keyboard_json())
    check("delivery slots", kb_delivery_channel_slots(snapshot), max_ui.delivery_slots_attachment(), vk_ui.vk_delivery_slots_keyboard_json())
    check("delivery select morning", kb_delivery_channel_select("morning", snapshot), max_ui.delivery_channel_select_attachment("morning"), vk_ui.vk_delivery_channel_select_keyboard_json("morning"))
    check("delivery select evening", kb_delivery_channel_select("evening", snapshot), max_ui.delivery_channel_select_attachment("evening"), vk_ui.vk_delivery_channel_select_keyboard_json("evening"))
    check("after post actions", kb_after_post_actions(), max_ui.post_actions_attachment(), vk_ui.vk_post_actions_keyboard_json())
    check("sales offer", kb_sales_offer(123), max_ui.sales_offer_attachment(), vk_ui.vk_sales_offer_keyboard_json())
    check("settings locked", kb_settings_locked(), max_ui.settings_locked_attachment(), vk_ui.vk_settings_locked_keyboard_json())
    check("ref bonus actions", kb_ref_bonus_actions(), max_ui.ref_bonus_actions_attachment(), vk_ui.vk_ref_bonus_actions_keyboard_json())

    print("✅ full rich public Telegram=VK=MAX button parity OK")


if __name__ == "__main__":
    main()
