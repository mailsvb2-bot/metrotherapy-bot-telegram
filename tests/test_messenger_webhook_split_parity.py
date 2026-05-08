from __future__ import annotations

import json

from runtime import messenger_webhooks as legacy
from runtime import messenger_payloads, messenger_vk_ui


def _json(value: str):
    return json.loads(value)


def test_vk_keyboard_helpers_preserve_json_payloads():
    assert _json(messenger_vk_ui.vk_default_keyboard_json()) == _json(legacy._vk_default_keyboard_json())
    assert _json(messenger_vk_ui.vk_demo_kind_keyboard_json()) == _json(legacy._vk_demo_kind_keyboard_json())
    assert _json(messenger_vk_ui.vk_weather_keyboard_json()) == _json(legacy._vk_weather_keyboard_json())
    assert _json(messenger_vk_ui.vk_weather_city_keyboard_json()) == _json(legacy._vk_weather_city_keyboard_json())
    assert _json(messenger_vk_ui.vk_score_scale_keyboard_json()) == _json(legacy._vk_score_scale_keyboard_json())


def test_vk_keyboard_enrichment_preserves_runtime_behavior():
    base = {"parse_mode": "HTML"}
    assert messenger_vk_ui.vk_text_send_kwargs("vk") == legacy._vk_text_send_kwargs("vk")
    assert messenger_vk_ui.vk_text_send_kwargs("max") == legacy._vk_text_send_kwargs("max")
    assert messenger_vk_ui.with_vk_keyboard("vk", base) == legacy._with_vk_keyboard("vk", base)
    assert messenger_vk_ui.with_vk_keyboard("max", base) == legacy._with_vk_keyboard("max", base)


def test_text_normalisation_preserves_known_aliases():
    samples = [
        "/start",
        "🌿 Попробовать бесплатно",
        "1️⃣ Утро / дорога",
        "2️⃣ Вечер / домой",
        "🔐 Полный маршрут",
        "💳 Оплатить",
        "🎁 Подарить",
        "📊 Прогресс",
        "🔁 Другой мессенджер",
        "unknown free text",
    ]
    for sample in samples:
        assert messenger_payloads.normalise_messenger_text(sample) == legacy._normalise_messenger_text(sample)


def test_vk_payload_text_extraction_preserves_nested_payloads():
    samples = [
        None,
        "",
        '{"command":"demo"}',
        {"payload": {"command": "weather_city"}},
        {"action": {"value": "progress"}},
        "plain text",
    ]
    for sample in samples:
        assert messenger_payloads.text_from_vk_payload(sample) == legacy._text_from_vk_payload(sample)


def test_message_extractors_preserve_vk_and_max_payloads():
    vk_payload = {
        "event_id": "evt-1",
        "object": {
            "message": {
                "id": 10,
                "from_id": 123,
                "date": 1710000000,
                "text": "🌿 Попробовать бесплатно",
            }
        },
    }
    max_payload = {
        "update_id": "u1",
        "message": {
            "message_id": "m1",
            "created_at": "2026-05-08T00:00:00Z",
            "sender": {"user_id": 456, "username": "u", "first_name": "Ivan", "last_name": "Petrov"},
            "body": {"text": "pay"},
        },
    }
    assert messenger_payloads.vk_event_key(vk_payload) == legacy._vk_event_key(vk_payload)
    assert messenger_payloads.max_event_key(max_payload) == legacy._max_event_key(max_payload)
    assert messenger_payloads.extract_vk_message(vk_payload) == legacy._extract_vk_message(vk_payload)
    assert messenger_payloads.extract_max_message(max_payload) == legacy._extract_max_message(max_payload)
