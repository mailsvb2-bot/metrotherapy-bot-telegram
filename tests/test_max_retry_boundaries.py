from __future__ import annotations

import asyncio
import urllib.error

import pytest

from runtime.messenger_max_sender import MaxBotSender
from runtime.messenger_transport_errors import MessengerTransportError


def test_max_media_permanent_http_error_does_not_enter_attachment_retry_loop(monkeypatch) -> None:
    calls = 0

    def fake_json_request(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            "https://platform-api2.max.ru/messages",
            401,
            "unauthorized",
            hdrs=None,
            fp=None,
        )

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("MAX_CA_BUNDLE", raising=False)
    monkeypatch.setattr("runtime.messenger_max_sender.json_request", fake_json_request)
    monkeypatch.setattr("runtime.messenger_max_sender.asyncio.sleep", no_sleep)

    with pytest.raises(MessengerTransportError, match="HTTP 401"):
        asyncio.run(
            MaxBotSender(token="bot-token")._send_media_payload(
                "123",
                text="audio",
                media_type="audio",
                media_token="media-token",
            )
        )

    assert calls == 1
