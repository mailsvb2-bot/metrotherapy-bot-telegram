from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from runtime.messenger_transport_errors import (
    MessengerMediaTokenRejectedError,
    MessengerTransportError,
)
from runtime.messenger_vk_sender import VkBotSender


def test_vk_save_shape_error_never_embeds_access_key() -> None:
    with pytest.raises(MessengerTransportError) as raised:
        VkBotSender._doc_attachment_from_save_response(
            {"response": {"access_key": "provider-secret-access-key"}}
        )

    assert "provider-secret-access-key" not in str(raised.value)
    assert raised.value.safe_code == "vk.docs_save.attachment_missing"


def test_vk_method_reduces_provider_error_to_safe_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "runtime.messenger_vk_sender.form_request",
        lambda *_args, **_kwargs: {
            "error": {
                "error_code": 5,
                "error_msg": "User authorization failed: access_token=provider-secret",
            }
        },
    )

    with pytest.raises(MessengerTransportError) as raised:
        asyncio.run(VkBotSender(token="bot-secret")._vk_method("users.get", {}))

    assert "provider-secret" not in str(raised.value)
    assert "bot-secret" not in str(raised.value)
    assert raised.value.safe_code == "vk.users_get.5"


def test_vk_messages_send_identifies_rejected_attachment_without_leaking_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "runtime.messenger_vk_sender.form_request",
        lambda *_args, **_kwargs: {
            "error": {
                "error_code": 100,
                "error_msg": "invalid attachment doc1_2_provider-secret-access-key",
            }
        },
    )

    with pytest.raises(MessengerMediaTokenRejectedError) as raised:
        asyncio.run(
            VkBotSender(token="bot-secret")._vk_method(
                "messages.send",
                {"attachment": "doc1_2_provider-secret-access-key"},
            )
        )

    assert "provider-secret" not in str(raised.value)
    assert raised.value.safe_code == "vk.messages.send.attachment_rejected"


def test_vk_attachment_recovery_invalidates_cache_and_reuploads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "audio.ogg"
    source.write_bytes(b"audio")
    attachments = iter(["doc1_1_cached", "doc1_2_fresh"])
    sent: list[str] = []
    invalidated: list[tuple[str, Path, str]] = []

    async def attachment_factory() -> str:
        return next(attachments)

    async def fake_send_text(
        _self: VkBotSender,
        _user_id: str,
        _caption: str,
        *,
        attachment: str,
        **_kwargs,
    ):
        sent.append(attachment)
        if attachment.endswith("cached"):
            raise MessengerMediaTokenRejectedError(
                "VK media token rejected",
                code="vk.messages.send.attachment_rejected",
            )
        return {"ok": True}

    monkeypatch.setattr(VkBotSender, "send_text", fake_send_text)
    monkeypatch.setattr(
        "runtime.messenger_vk_sender.invalidate_media_token",
        lambda platform, path, *, media_type: invalidated.append((platform, path, media_type)) or True,
    )

    result = asyncio.run(
        VkBotSender(token="bot-secret")._send_with_attachment_recovery(
            "1",
            source,
            caption="caption",
            cache_media_type="audio:audio_message",
            attachment_factory=attachment_factory,
        )
    )

    assert result == {"ok": True}
    assert sent == ["doc1_1_cached", "doc1_2_fresh"]
    assert invalidated == [("vk", source, "audio:audio_message")]
