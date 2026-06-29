from __future__ import annotations

import json

from runtime.messenger_senders import _strip_one_time_from_vk_inline_keyboard, _vk_provider_keyboard_json
from runtime.messenger_vk_ui import vk_weather_keyboard_json


def test_vk_inline_keyboard_payload_drops_one_time_before_provider_send() -> None:
    raw = json.dumps(
        {
            "one_time": True,
            "inline": True,
            "buttons": [[{"action": {"type": "callback", "label": "Back", "payload": "{}"}}]],
        }
    )

    payload = json.loads(_strip_one_time_from_vk_inline_keyboard(raw))

    assert payload["inline"] is True
    assert "one_time" not in payload


def test_vk_weather_keyboard_is_provider_valid_after_facade_normalization() -> None:
    wire = json.loads(_vk_provider_keyboard_json(vk_weather_keyboard_json(), external_user_id="123", text="weather"))

    assert wire["inline"] is True
    assert "one_time" not in wire
    assert wire["buttons"]
