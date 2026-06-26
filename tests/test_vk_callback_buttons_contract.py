from __future__ import annotations

import json

from keyboards.inline import kb_main
from runtime.messenger_ingress import VK_PROCESSABLE_EVENT_TYPES, _vk_dedupe_key
from runtime.messenger_payloads import extract_vk_message
from runtime.telegram_button_parity import vk_keyboard_from_telegram


def test_vk_parity_keyboard_uses_callback_buttons() -> None:
    keyboard = json.loads(vk_keyboard_from_telegram(kb_main(None)))
    assert keyboard["inline"] is True
    actions = [button["action"] for row in keyboard["buttons"] for button in row]
    assert actions
    assert all(action["type"] == "callback" for action in actions)


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
    extracted = extract_vk_message(payload)
    assert extracted is not None
    assert extracted["external_user_id"] == "123"
    assert extracted["text"] == "settings"


def test_vk_message_event_dedupe_key_uses_event_id_not_only_user_id() -> None:
    first = {"type": "message_event", "object": {"event_id": "evt-1", "user_id": 123, "payload": {"command": "start"}}}
    second = {"type": "message_event", "object": {"event_id": "evt-2", "user_id": 123, "payload": {"command": "start"}}}
    assert _vk_dedupe_key(first) != _vk_dedupe_key(second)
