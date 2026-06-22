from __future__ import annotations

import json

from runtime import messenger_payloads, messenger_vk_ui


def _json(value: str):
    return json.loads(value)


def _keyboard_buttons(keyboard_json: str) -> list[dict]:
    keyboard = _json(keyboard_json)
    return [button for row in keyboard["buttons"] for button in row]


def _button_label(button: dict) -> str:
    return str(button["action"]["label"])


def _button_command(button: dict) -> str:
    return messenger_payloads.text_from_vk_payload(button["action"].get("payload"))


def test_vk_keyboard_helpers_preserve_json_payloads():
    assert _json(messenger_vk_ui.vk_default_keyboard_json())["buttons"]
    assert _json(messenger_vk_ui.vk_demo_kind_keyboard_json())["buttons"]
    assert _json(messenger_vk_ui.vk_weather_keyboard_json())["buttons"]
    assert _json(messenger_vk_ui.vk_weather_city_keyboard_json())["buttons"]
    assert _json(messenger_vk_ui.vk_score_scale_keyboard_json())["buttons"]


def test_vk_keyboard_buttons_have_payloads_matching_canonical_normalizer():
    keyboards = [
        messenger_vk_ui.vk_default_keyboard_json(),
        messenger_vk_ui.vk_demo_kind_keyboard_json(),
        messenger_vk_ui.vk_weather_keyboard_json(),
        messenger_vk_ui.vk_weather_city_keyboard_json(),
        messenger_vk_ui.vk_score_scale_keyboard_json(),
    ]
    for keyboard_json in keyboards:
        for button in _keyboard_buttons(keyboard_json):
            action = button["action"]
            if action["type"] != "text":
                continue
            label_command = messenger_payloads.normalise_messenger_text(_button_label(button))
            payload_command = _button_command(button)
            assert payload_command
            assert label_command == payload_command


def test_vk_keyboard_enrichment_preserves_runtime_behavior():
    base = {"parse_mode": "HTML"}
    assert messenger_vk_ui.vk_text_send_kwargs("vk")["keyboard_json"]
    assert messenger_vk_ui.vk_text_send_kwargs("max") == {}
    vk_enriched = messenger_vk_ui.with_vk_keyboard("vk", base)
    assert vk_enriched["parse_mode"] == "HTML"
    assert vk_enriched["keyboard_json"]
    assert messenger_vk_ui.with_vk_keyboard("max", base) == base


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
        "📈 Мой прогресс",
        "🧾 История",
        "🔁 Другой мессенджер",
        "⬅️ Меню",
        "🔄 Обновить погоду",
        "🏙 Изменить город",
        "unknown free text",
    ]
    normalized = [messenger_payloads.normalise_messenger_text(sample) for sample in samples]
    assert normalized == [
        "start",
        "demo",
        "demo_work",
        "demo_home",
        "full",
        "pay",
        "gift",
        "progress",
        "progress",
        "history",
        "switch",
        "start",
        "weather",
        "weather_city",
        "unknown free text",
    ]


def test_vk_payload_text_extraction_preserves_nested_payloads():
    assert messenger_payloads.text_from_vk_payload(None) == ""
    assert messenger_payloads.text_from_vk_payload("") == ""
    assert messenger_payloads.text_from_vk_payload('{"command":"demo"}') == "demo"
    assert messenger_payloads.text_from_vk_payload({"payload": {"command": "weather_city"}}) == "weather_city"
    assert messenger_payloads.text_from_vk_payload({"action": {"value": "progress"}}) == "progress"
    assert messenger_payloads.text_from_vk_payload("plain text") == "plain text"


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
    assert messenger_payloads.vk_event_key(vk_payload) == "evt-1:10:123:1710000000"
    assert messenger_payloads.max_event_key(max_payload) == "u1:m1:456:2026-05-08T00:00:00Z"
    assert messenger_payloads.extract_vk_message(vk_payload)["user_id"] == 123
    assert messenger_payloads.extract_max_message(max_payload)["user_id"] == 456


def test_max_score_scale_buttons_are_native_and_payload_safe_for_all_values():
    from runtime import messenger_max_ui

    attachments = messenger_max_ui.native_keyboard_attachments(
        "Шкала оценки после прослушивания:\n−10 — хуже, +10 — лучше"
    )
    assert attachments

    buttons = attachments[0]["payload"]["buttons"]
    flat = [button for row in buttons for button in row]
    by_score = {}
    for button in flat:
        payload_command = str(button.get("payload", {}).get("command", ""))
        if not payload_command.startswith("score:"):
            continue
        score = messenger_payloads.normalise_messenger_text(payload_command)
        by_score[score] = button

    expected = {str(value) for value in range(-10, 11)}
    assert set(by_score) == expected

    for value in range(-10, 11):
        score = str(value)
        assert by_score[score]["text"] == str(value)
        assert by_score[score]["payload"]["command"] == f"score:{value}"

        stale_payload = {
            "update_type": "message_created",
            "message": {
                "message_id": f"m-score-{value}",
                "created_at": "2026-05-15T00:00:00Z",
                "sender": {"user_id": 456},
                "body": {
                    "text": "start",
                    "payload": {"command": f"score:{value}"},
                },
            },
        }

        extracted = messenger_payloads.extract_max_message(stale_payload)
        assert extracted["text"] == score
