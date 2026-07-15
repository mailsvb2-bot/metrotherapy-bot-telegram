from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# This verifier exercises payload extraction, which consults pending mood state.
# Keep it hermetic so a local quality run never reads or mutates production data.
_TMP_DB_DIR = tempfile.TemporaryDirectory(prefix="metrotherapy_button_parity_")
os.environ["APP_ENV"] = "test"
os.environ["LOAD_DOTENV"] = "0"
os.environ["METRO_DB_ENGINE"] = "sqlite"
os.environ["METRO_DB_PATH"] = str(Path(_TMP_DB_DIR.name) / "parity.db")

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
from runtime.messenger_payloads import (
    extract_max_message,
    extract_vk_message,
    normalise_messenger_text,
)
from runtime.telegram_button_parity import canonical_button_command
from services.schema import init_db


def tg(markup: Any) -> list[list[tuple[str, str]]]:
    return [[(str(b.text), str(b.callback_data or "")) for b in row] for row in markup.inline_keyboard]


def mx(attachment: dict[str, Any]) -> list[list[tuple[str, str]]]:
    return [
        [(str(b["text"]), str((b.get("payload") or {}).get("command") or "")) for b in row]
        for row in attachment["payload"]["buttons"]
    ]


def vk(keyboard_json: str) -> list[list[tuple[str, str]]]:
    rows = json.loads(keyboard_json)["buttons"]
    out: list[list[tuple[str, str]]] = []
    for row in rows:
        out_row: list[tuple[str, str]] = []
        for b in row:
            payload = json.loads(b["action"].get("payload") or "{}")
            out_row.append((str(b["action"]["label"]), str(payload.get("command") or "")))
        out.append(out_row)
    return out


def flat(rows: list[list[tuple[str, str]]]) -> list[tuple[str, str]]:
    return [item for row in rows for item in row]


def semantic_commands(rows: list[list[tuple[str, str]]]) -> list[str]:
    commands: list[str] = []
    for _label, command in flat(rows):
        canonical = canonical_button_command(command)
        commands.append(normalise_messenger_text(canonical, allow_plain_score=True))
    return commands


def contains_in_order(actual: list[str], required: list[str]) -> bool:
    cursor = iter(actual)
    return all(any(candidate == wanted for candidate in cursor) for wanted in required)


def eq(name: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise AssertionError(f"{name} mismatch actual={actual!r} expected={expected!r}")


def check(name: str, expected_markup: Any, max_attachment: dict[str, Any] | None, vk_keyboard: str | None) -> None:
    required = semantic_commands(tg(expected_markup))
    if max_attachment is not None:
        actual = semantic_commands(mx(max_attachment))
        if not contains_in_order(actual, required):
            raise AssertionError(f"MAX {name} missing required actions actual={actual!r} required={required!r}")
    if vk_keyboard is not None:
        actual = semantic_commands(vk(vk_keyboard))
        if not contains_in_order(actual, required):
            raise AssertionError(f"VK {name} missing required actions actual={actual!r} required={required!r}")


def check_payloads() -> None:
    cases = {
        "mood:post:123:1": "1",
        "mood:post:123:-5": "-5",
        "mood:pre:123:2": "2",
        "score:1": "1",
        "score:-2": "-2",
        "1": "demo_work",
        "2": "demo_home",
        "demo_kind_work": "demo_work",
        "demo_kind_home": "demo_home",
        "weather:city": "weather_city",
        "sub:menu": "pay",
        "gift:menu": "gift",
        "settings:state": "progress",
        "menu:main": "start",
    }
    for raw, expected in cases.items():
        eq(f"payload {raw}", normalise_messenger_text(raw), expected)

    eq("context score 1", normalise_messenger_text("1", allow_plain_score=True), "1")
    eq("context score 2", normalise_messenger_text("2", allow_plain_score=True), "2")

    vk_score = extract_vk_message(
        {
            "object": {
                "message": {
                    "from_id": 987654321,
                    "text": "1",
                    "payload": json.dumps({"command": "mood:post:123:1"}, ensure_ascii=False),
                }
            }
        }
    )
    if vk_score is None:
        raise AssertionError("VK score fixture returned None")
    eq("VK payload mood post score", vk_score["text"], "1")

    vk_demo = extract_vk_message(
        {
            "object": {
                "message": {
                    "from_id": 987654322,
                    "text": "1",
                    "payload": json.dumps({"command": "demo_kind_work"}, ensure_ascii=False),
                }
            }
        }
    )
    if vk_demo is None:
        raise AssertionError("VK demo fixture returned None")
    eq("VK payload demo kind", vk_demo["text"], "demo_work")

    max_score = extract_max_message(
        {
            "message": {"sender": {"user_id": 987654323}, "text": "1"},
            "callback": {"payload": {"command": "mood:post:123:1"}},
        }
    )
    if max_score is None:
        raise AssertionError("MAX score fixture returned None")
    eq("MAX payload mood post score", max_score["text"], "1")

    max_demo = extract_max_message(
        {
            "message": {"sender": {"user_id": 987654324}, "text": "1"},
            "callback": {"payload": {"command": "demo_kind_work"}},
        }
    )
    if max_demo is None:
        raise AssertionError("MAX demo fixture returned None")
    eq("MAX payload demo kind", max_demo["text"], "demo_work")


def main() -> None:
    init_db()
    check("main", kb_main(None), max_ui.main_menu_attachment(), vk_ui.vk_main_keyboard_json(None))
    check("demo", kb_demo_kind(), max_ui.demo_kind_attachment(), vk_ui.vk_demo_kind_keyboard_json())
    check("weather", kb_weather(), max_ui.weather_attachment(), vk_ui.vk_weather_keyboard_json())
    check("full access", kb_full_access_menu(), max_ui.full_access_attachment(), vk_ui.vk_full_access_keyboard_json())
    check("mood pre", kb_mood_scale(123, stage="pre"), max_ui.score_scale_attachment(123, stage="pre"), vk_ui.vk_score_scale_keyboard_json(123, stage="pre"))
    check("mood post", kb_mood_scale(123, stage="post"), max_ui.score_scale_attachment(123, stage="post"), vk_ui.vk_score_scale_keyboard_json(123, stage="post"))
    check("mood done", kb_mood_done(123), max_ui.post_audio_attachment(123), vk_ui.vk_post_audio_keyboard_json(123))
    check("state period", kb_state_period_menu(), max_ui.state_period_attachment(), vk_ui.vk_state_period_keyboard_json())

    menu_text = "Главное меню\n\nВыберите маршрут.\n\n• 📈 Мой прогресс"
    main_commands = semantic_commands(tg(kb_main(None)))
    eq(
        "VK runtime main",
        semantic_commands(vk(vk_ui.with_vk_keyboard("vk", {"_text_for_keyboard": menu_text})["keyboard_json"])),
        main_commands,
    )
    attachments = max_ui.native_keyboard_attachments(menu_text)
    if not attachments:
        raise AssertionError("MAX runtime main produced no attachment")
    eq("MAX runtime main", semantic_commands(mx(attachments[0])), main_commands)

    check_payloads()
    print("OK messenger parity and payload verifier")


if __name__ == "__main__":
    main()
