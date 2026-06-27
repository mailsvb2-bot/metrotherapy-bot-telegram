from __future__ import annotations

from runtime.messenger_payloads import extract_max_message, extract_vk_message, max_event_key, vk_event_key


def test_max_payload_helpers_fail_closed_on_non_dict_message():
    payload = {"update_id": "bad-1", "message": "not-a-dict"}

    assert max_event_key(payload) == "bad-1"
    assert extract_max_message(payload) is None


def test_max_payload_helpers_ignore_non_dict_body():
    payload = {
        "update_id": "bad-2",
        "message": {"body": "not-a-dict", "sender": {"user_id": 123}},
    }

    assert max_event_key(payload) == "bad-2:123"
    assert extract_max_message(payload) == {
        "user_id": 123,
        "external_user_id": "123",
        "username": None,
        "display_name": None,
        "first_name": None,
        "text": "start",
    }


def test_vk_payload_helpers_fail_closed_on_non_dict_message():
    payload = {"event_id": "vk-bad-1", "object": {"message": "not-a-dict"}}

    assert vk_event_key(payload) == "vk-bad-1"
    assert extract_vk_message(payload) is None
