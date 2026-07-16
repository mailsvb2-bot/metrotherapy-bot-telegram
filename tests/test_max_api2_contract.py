from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from runtime.messenger_max_sender import MAX_API2_BASE_URL, MaxBotSender, _attachment_retry_delays
from scripts import register_max_webhook
from services.messenger import preflight


ROOT = Path(__file__).resolve().parents[1]


def test_max_sender_defaults_and_legacy_values_resolve_to_api2(monkeypatch) -> None:
    monkeypatch.delenv("MAX_API_BASE_URL", raising=False)
    monkeypatch.setattr("runtime.messenger_max_sender.settings.MAX_API_BASE_URL", "", raising=False)

    assert MaxBotSender()._api_base() == MAX_API2_BASE_URL
    assert MaxBotSender(api_base_url="https://platform-api.max.ru")._api_base() == MAX_API2_BASE_URL
    assert MaxBotSender(api_base_url="https://botapi.max.ru")._api_base() == MAX_API2_BASE_URL


def test_max_attachment_retry_schedule_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("MAX_ATTACHMENT_RETRY_DELAYS_SEC", "0,1,2.5,broken,-1,90")

    assert _attachment_retry_delays() == (0.0, 1.0, 2.5, 60.0)


def test_max_multipart_upload_does_not_receive_bot_token(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"audio")
    captured: dict[str, object] = {}

    monkeypatch.setattr("runtime.messenger_max_sender.get_cached_media_token", lambda *args, **kwargs: None)
    monkeypatch.setattr("runtime.messenger_max_sender.store_media_token", lambda *args, **kwargs: None)

    def fake_json_request(url, *, method, headers, payload):
        captured["api_headers"] = headers
        return {"url": "https://upload.max.example/file", "token": "media-token"}

    def fake_multipart_upload(url, *, field_name, path, timeout=120):
        captured["upload_url"] = url
        captured["field_name"] = field_name
        captured["path"] = path
        captured["timeout"] = timeout
        return {"token": "media-token"}

    monkeypatch.setattr("runtime.messenger_max_sender.json_request", fake_json_request)
    monkeypatch.setattr("runtime.messenger_max_sender.multipart_upload", fake_multipart_upload)

    result = asyncio.run(MaxBotSender(token="bot-secret")._ensure_media_token(source, media_type="audio"))

    assert result == "media-token"
    assert captured["api_headers"] == {"Authorization": "bot-secret"}
    assert captured["upload_url"] == "https://upload.max.example/file"
    assert "bot-secret" not in repr(captured)


def test_max_registration_uses_api2_and_validates_secret(monkeypatch) -> None:
    monkeypatch.setenv("MAX_BOT_TOKEN", "token")
    monkeypatch.setenv("MAX_WEBHOOK_SECRET", "valid_secret-1")
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example.test")
    monkeypatch.delenv("MAX_API_BASE_URL", raising=False)

    config = register_max_webhook._load_config()

    assert config.api_base_url == "https://platform-api2.max.ru"
    assert config.webhook_url == "https://bot.example.test/webhooks/max"

    monkeypatch.setenv("MAX_WEBHOOK_SECRET", "bad secret")
    with pytest.raises(SystemExit, match="MAX_WEBHOOK_SECRET"):
        register_max_webhook._load_config()


def test_max_registration_rejects_legacy_api_domain(monkeypatch) -> None:
    monkeypatch.setenv("MAX_BOT_TOKEN", "token")
    monkeypatch.setenv("MAX_WEBHOOK_SECRET", "valid_secret-1")
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example.test")
    monkeypatch.setenv("MAX_API_BASE_URL", "https://platform-api.max.ru")

    with pytest.raises(SystemExit, match="platform-api2.max.ru"):
        register_max_webhook._load_config()


def test_max_preflight_warns_on_legacy_api_domain(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("MAX_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("MAX_API_BASE_URL", "https://platform-api.max.ru")
    monkeypatch.setattr(preflight.settings, "MESSENGER_PUBLIC_BASE_URL", "https://bot.example.test", raising=False)
    monkeypatch.setattr(preflight.settings, "MAX_BOT_TOKEN", "token", raising=False)
    monkeypatch.setattr(preflight.settings, "MAX_BOT_LINK_BASE", "https://max.ru/bot/{payload}", raising=False)

    status = preflight.check_max_preflight()

    assert status.ok is True
    assert any("platform-api2.max.ru" in warning for warning in status.warnings)


def test_max_production_files_use_api2_and_no_query_secret_helper() -> None:
    paths = [
        ROOT / "deploy" / "metrotherapy.env.example",
        ROOT / "docs" / "MAX_WEBHOOK_SETUP.md",
        ROOT / "scripts" / "register_max_webhook.py",
    ]
    for path in paths:
        content = path.read_text(encoding="utf-8")
        assert "https://platform-api2.max.ru" in content

    assert not (ROOT / "scripts" / "configure_max_webhook.py").exists()
    docs = (ROOT / "docs" / "MAX_WEBHOOK_SETUP.md").read_text(encoding="utf-8")
    assert "X-Max-Bot-Api-Secret" in docs
    assert "?secret=" not in docs


def test_max_sender_source_does_not_forward_token_to_upload_url() -> None:
    source = (ROOT / "runtime" / "messenger_max_sender.py").read_text(encoding="utf-8")

    assert "multipart_upload,\n            upload_url,\n            field_name=\"data\"" in source
    assert "multipart_upload, upload_url, token=token" not in source
