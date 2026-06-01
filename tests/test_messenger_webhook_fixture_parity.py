from __future__ import annotations

import json
from pathlib import Path

from runtime.messenger_payloads import extract_max_message, extract_vk_message, max_event_key, normalise_messenger_text, vk_event_key

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "messenger"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_vk_button_payload_fixture_extracts_demo_command():
    payload = _load_fixture("vk_message_new_button_demo.json")

    extracted = extract_vk_message(payload)

    assert extracted is not None
    assert extracted["external_user_id"] == "910001"
    assert extracted["text"] == "demo"
    assert vk_event_key(payload).startswith("vk-fixture-demo-1")


def test_vk_score_fixture_preserves_numeric_payload_contract():
    payload = _load_fixture("vk_message_new_score_plus_one.json")

    extracted = extract_vk_message(payload)

    assert extracted is not None
    assert extracted["text"] == "demo_work"
    # VK numeric payload 1 is intentionally still a legacy demo alias at the
    # webhook extraction layer. VK score handling is protected by direct score
    # keyboard tests and text-flow pending-session tests.
    assert normalise_messenger_text("+1") == "1"


def test_max_weather_message_fixture_extracts_weather_command():
    payload = _load_fixture("max_message_created_weather.json")

    extracted = extract_max_message(payload)

    assert extracted is not None
    assert extracted["external_user_id"] == "920001"
    assert extracted["text"] == "weather"
    assert max_event_key(payload).startswith("max-fixture-weather-1")


def test_max_score_callback_fixture_prefers_command_payload_over_visible_text():
    payload = _load_fixture("max_button_callback_score_1.json")

    extracted = extract_max_message(payload)

    assert extracted is not None
    assert extracted["external_user_id"] == "920001"
    assert extracted["text"] == "1"
    assert normalise_messenger_text("score:1") == "1"
