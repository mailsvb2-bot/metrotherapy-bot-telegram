from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from runtime.messenger_max_sender import MaxBotSender
from runtime.messenger_transport_errors import (
    MessengerMediaTokenRejectedError,
    MessengerTransportError,
)


def test_max_upload_shape_error_never_embeds_provider_payload_secrets() -> None:
    with pytest.raises(MessengerTransportError) as raised:
        MaxBotSender._upload_payload(
            {"url": "https://upload.example/secret-capability", "token": ""},
            {"error": "provider-secret-body"},
            media_type="audio",
        )

    message = str(raised.value)
    assert "provider-secret-body" not in message
    assert "secret-capability" not in message
    assert raised.value.safe_code == "max.audio_upload.token_missing"


def test_max_send_text_error_is_reduced_to_safe_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setattr(
        "runtime.messenger_max_sender.json_request",
        lambda *_args, **_kwargs: {
            "error": {"code": "permission.denied", "message": "token=provider-secret"}
        },
    )

    with pytest.raises(MessengerTransportError) as raised:
        asyncio.run(MaxBotSender(token="bot-secret").send_text("1", "hello"))

    assert "provider-secret" not in str(raised.value)
    assert "bot-secret" not in str(raised.value)
    assert raised.value.safe_code == "max.send_text.permission.denied"


def test_rejected_cached_max_media_token_is_invalidated_and_rebuilt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"audio")
    issued = iter(["cached-token", "fresh-token"])
    sent_tokens: list[str] = []
    invalidated: list[tuple[str, Path, str]] = []

    async def fake_ensure(_path: Path, *, media_type: str) -> str:
        assert media_type == "audio"
        return next(issued)

    async def fake_send(
        _external_user_id: str,
        *,
        text: str,
        media_type: str,
        media_token: str,
        notify,
    ):
        assert text == "caption"
        assert media_type == "audio"
        sent_tokens.append(media_token)
        if media_token == "cached-token":
            raise MessengerMediaTokenRejectedError(
                "MAX media token rejected",
                code="max.attachment.invalid_token",
            )
        return {"ok": True}

    monkeypatch.setattr(MaxBotSender, "_ensure_media_token", fake_ensure)
    monkeypatch.setattr(MaxBotSender, "_send_media_payload", fake_send)
    monkeypatch.setattr(
        "runtime.messenger_max_sender.invalidate_media_token",
        lambda platform, path, *, media_type: invalidated.append((platform, path, media_type)) or True,
    )

    result = asyncio.run(
        MaxBotSender(token="bot-secret")._send_media_file(
            "1",
            source,
            media_type="audio",
            caption="caption",
            notify=None,
        )
    )

    assert result == {"ok": True}
    assert sent_tokens == ["cached-token", "fresh-token"]
    assert invalidated == [("max", source, "audio")]
