from __future__ import annotations

import json

from keyboards.inline import kb_main
from runtime.messenger_ingress import (
    VK_PROCESSABLE_EVENT_TYPES,
    _entry_start_text,
    _vk_dedupe_key,
    _vk_event_context,
    _vk_score_route_text,
)
from runtime.messenger_payloads import extract_vk_message
from runtime.messenger_vk_sender import _callback_keyboard_json
from runtime.telegram_button_parity import vk_keyboard_from_telegram


def test_vk_parity_keyboard_uses_callback_buttons() -> None:
    keyboard = json.loads(vk_keyboard_from_telegram(kb_main(None)))
    assert keyboard["inline"] is True
    actions = [button["action"] for row in keyboard["buttons"] for button in row]
    assert actions
    assert all(action["type"] == "callback" for action in actions)


def test_vk_sender_normalizes_text_buttons_to_callback_buttons() -> None:
    raw = {
        "one_time": False,
        "inline": False,
        "buttons": [[{"action": {"type": "text", "label": "Settings", "payload": json.dumps({"command": "settings"})}}]],
    }
    keyboard = json.loads(_callback_keyboard_json(json.dumps(raw)))
    assert keyboard["inline"] is True
    assert keyboard["buttons"][0][0]["action"]["type"] == "callback"
    assert json.loads(keyboard["buttons"][0][0]["action"]["payload"])["command"] == "settings"


def test_vk_message_event_is_processable_and_extracts_payload_command() -> None:
    payload = {
        "type": "message_event",
        "object": {
            "event_id": "evt-1",
            "user_id": 123,
            "peer_id": 123,
            "payload": {"command": "settings"},
        },
    }
    assert "message_event" in VK_PROCESSABLE_EVENT_TYPES
    assert _vk_dedupe_key(payload) == "evt-1:123"
    assert _vk_event_context(payload) == ("evt-1", "123", "123")
    extracted = extract_vk_message(payload)
    assert extracted is not None
    assert extracted["external_user_id"] == "123"
    assert extracted["text"] == "settings"


def test_vk_message_event_score_one_two_are_preserved_as_score_not_demo() -> None:
    score_one = {
        "type": "message_event",
        "object": {
            "event_id": "evt-score-1",
            "user_id": 123,
            "peer_id": 123,
            "payload": {"command": "score:1"},
        },
    }
    score_two = {
        "type": "message_event",
        "object": {
            "event_id": "evt-score-2",
            "user_id": 123,
            "peer_id": 123,
            "payload": {"command": "score=2"},
        },
    }

    extracted_one = extract_vk_message(score_one)
    extracted_two = extract_vk_message(score_two)

    assert extracted_one is not None
    assert extracted_two is not None
    assert extracted_one["text"] == "1"
    assert extracted_two["text"] == "2"
    assert _entry_start_text(_vk_score_route_text(score_one) or extracted_one["text"]) == "+1"
    assert _entry_start_text(_vk_score_route_text(score_two) or extracted_two["text"]) == "+2"


def test_vk_message_event_dedupe_key_uses_event_id_not_only_user_id() -> None:
    first = {"type": "message_event", "object": {"event_id": "evt-1", "user_id": 123, "payload": {"command": "start"}}}
    second = {"type": "message_event", "object": {"event_id": "evt-2", "user_id": 123, "payload": {"command": "start"}}}
    assert _vk_dedupe_key(first) != _vk_dedupe_key(second)
