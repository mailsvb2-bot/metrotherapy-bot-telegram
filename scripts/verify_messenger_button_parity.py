from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from keyboards.inline import (
    kb_demo_kind,
    kb_full_access_menu,
    kb_main,
    kb_mood_done,
    kb_mood_scale,
    kb_state_period_menu,
    kb_weather,
)
from runtime import messenger_max_ui as max_ui
from runtime import messenger_vk_ui as vk_ui


TG_CALLBACK_TO_COMMAND = {
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

    # Telegram state-period callbacks do not exist as raw callback strings in
    # VK/MAX text transport. These are normalized to the current messenger
    # command semantics used by text_ui.py.
    "state:rate": "continue",
    "state:today": "progress",
    "state:yesterday": "history",
    "state:all": "progress",
}


def tg_rows(markup: Any) -> list[list[tuple[str, str]]]:
    out: list[list[tuple[str, str]]] = []
    for row in markup.inline_keyboard:
        result_row: list[tuple[str, str]] = []
        for button in row:
            callback = str(button.callback_data or "")
            if callback.startswith("mood:done"):
                command = "done"
            elif callback.startswith("mood:"):
                parts = callback.split(":")
                command = f"score:{parts[-1]}" if len(parts) >= 4 else callback
            else:
                command = TG_CALLBACK_TO_COMMAND.get(callback, callback)
            result_row.append((button.text, command))
        out.append(result_row)
    return out


def max_rows(attachment: dict[str, Any]) -> list[list[tuple[str, str]]]:
    rows = attachment["payload"]["buttons"]
    out: list[list[tuple[str, str]]] = []
    for row in rows:
        result_row: list[tuple[str, str]] = []
        for button in row:
            payload = button.get("payload") or {}
            result_row.append((button["text"], str(payload.get("command") or "")))
        out.append(result_row)
    return out


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
            result_row.append((action["label"], command))
        out.append(result_row)
    return out


def assert_equal(name: str, actual: list[list[tuple[str, str]]], expected: list[list[tuple[str, str]]]) -> None:
    if actual != expected:
        raise AssertionError(
            f"{name} mismatch\n"
            f"actual={actual!r}\n"
            f"expected={expected!r}"
        )


def main() -> None:
    assert_equal("MAX main", max_rows(max_ui.main_menu_attachment()), tg_rows(kb_main(None)))
    assert_equal("VK main", vk_rows(vk_ui.vk_main_keyboard_json(None)), tg_rows(kb_main(None)))

    assert_equal("MAX demo", max_rows(max_ui.demo_kind_attachment()), tg_rows(kb_demo_kind()))
    assert_equal("VK demo", vk_rows(vk_ui.vk_demo_kind_keyboard_json()), tg_rows(kb_demo_kind()))

    assert_equal("MAX weather", max_rows(max_ui.weather_attachment()), tg_rows(kb_weather()))
    assert_equal("VK weather", vk_rows(vk_ui.vk_weather_keyboard_json()), tg_rows(kb_weather()))

    assert_equal("MAX full access", max_rows(max_ui.full_route_attachment()), tg_rows(kb_full_access_menu()))
    assert_equal("VK full access", vk_rows(vk_ui.full_route_keyboard_json()), tg_rows(kb_full_access_menu()))

    assert_equal("MAX mood scale", max_rows(max_ui.score_scale_attachment()), tg_rows(kb_mood_scale(123, stage="pre")))
    assert_equal("VK mood scale", vk_rows(vk_ui.vk_score_scale_keyboard_json()), tg_rows(kb_mood_scale(123, stage="pre")))

    assert_equal("MAX mood done", max_rows(max_ui.post_audio_attachment()), tg_rows(kb_mood_done(123)))

    assert_equal("MAX state period", max_rows(max_ui.state_period_attachment()), tg_rows(kb_state_period_menu()))
    assert_equal("VK state period", vk_rows(vk_ui.vk_state_period_keyboard_json()), tg_rows(kb_state_period_menu()))

    print("✅ messenger button parity OK")


if __name__ == "__main__":
    main()
