from __future__ import annotations

import pytest

from interfaces.messaging import MessengerButton, MessengerMessage, normalize_platform


def test_normalize_platform_aliases():
    assert normalize_platform("telegram") == "telegram"
    assert normalize_platform("tg") == "telegram"
    assert normalize_platform("VK") == "vk"
    assert normalize_platform("vkontakte") == "vk"
    assert normalize_platform("max.ru") == "max"


@pytest.mark.parametrize("raw", ["", "unknown", "whatsapp"])
def test_normalize_platform_rejects_unknown_values(raw):
    with pytest.raises(ValueError):
        normalize_platform(raw)


def test_url_button_requires_absolute_url():
    assert MessengerButton(label="Open", kind="url", payload="https://example.test").payload == "https://example.test"
    with pytest.raises(ValueError):
        MessengerButton(label="Open", kind="url", payload="/relative")


def test_callback_button_requires_payload():
    assert MessengerButton(label="Score 5", kind="callback", payload="score:5").payload == "score:5"
    with pytest.raises(ValueError):
        MessengerButton(label="Score", kind="callback", payload="")


def test_message_requires_user_and_content():
    with pytest.raises(ValueError):
        MessengerMessage(platform="telegram", external_user_id="", text="hello")
    with pytest.raises(ValueError):
        MessengerMessage(platform="telegram", external_user_id="123", text="")


def test_message_normalizes_platform_and_freezes_buttons_tuple():
    button = MessengerButton(label="Pay", kind="url", payload="https://pay.example.test")
    message = MessengerMessage(
        platform="tg",
        external_user_id="123",
        text="Choose package",
        buttons=(button,),
        metadata={"source": "test"},
    )

    assert message.platform == "telegram"
    assert message.has_buttons is True
    assert message.buttons == (button,)
    assert message.metadata == {"source": "test"}
