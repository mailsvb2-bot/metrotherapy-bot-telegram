from __future__ import annotations

import asyncio

from config.settings import settings
from runtime.messenger_ingress import _max_secret_ok, _vk_secret_ok
from runtime.messenger_webhooks import _max_webhook_with_official_secret, _vk_group_ok
from runtime.telegram_webhook_runtime import telegram_secret_ok


class DummyRequest:
    def __init__(self, *, headers: dict[str, str] | None = None, query: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.query = query or {}

    def clone(self, *, headers: dict[str, str]):
        return DummyRequest(headers=dict(headers), query=dict(self.query))


def test_telegram_webhook_secret_missing_is_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_INSECURE_TELEGRAM_WEBHOOK", raising=False)
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
    assert telegram_secret_ok(DummyRequest()) is False


def test_telegram_webhook_secret_dev_escape_hatch(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("ALLOW_INSECURE_TELEGRAM_WEBHOOK", "1")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
    assert telegram_secret_ok(DummyRequest()) is True


def test_telegram_webhook_secret_uses_constant_time_compare(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "expected")
    assert telegram_secret_ok(DummyRequest(headers={"X-Telegram-Bot-Api-Secret-Token": "expected"})) is True
    assert telegram_secret_ok(DummyRequest(headers={"X-Telegram-Bot-Api-Secret-Token": "bad"})) is False


def test_max_webhook_secret_missing_is_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_INSECURE_MESSENGER_WEBHOOKS", raising=False)
    monkeypatch.setattr(settings, "MAX_WEBHOOK_SECRET", "")
    assert _max_secret_ok(DummyRequest(), {}) is False


def test_max_webhook_secret_dev_escape_hatch(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("ALLOW_INSECURE_MESSENGER_WEBHOOKS", "1")
    monkeypatch.setattr(settings, "MAX_WEBHOOK_SECRET", "")
    assert _max_secret_ok(DummyRequest(), {}) is True


def test_max_webhook_secret_match(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(settings, "MAX_WEBHOOK_SECRET", "expected")
    assert _max_secret_ok(DummyRequest(headers={"X-Max-Webhook-Secret": "expected"}), {}) is True
    assert _max_secret_ok(DummyRequest(headers={"X-Max-Webhook-Secret": "bad"}), {}) is False


def test_max_webhook_official_secret_header_is_mapped(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_max_webhook(request):
        captured.update(dict(request.headers))
        return "ok"

    monkeypatch.setattr("runtime.messenger_webhooks.max_webhook", fake_max_webhook)
    result = asyncio.run(
        _max_webhook_with_official_secret(
            DummyRequest(headers={"X-Max-Bot-Api-Secret": "expected"})
        )
    )

    assert result == "ok"
    assert captured["X-Max-Webhook-Secret"] == "expected"


def test_vk_webhook_secret_missing_is_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("ALLOW_INSECURE_MESSENGER_WEBHOOKS", raising=False)
    monkeypatch.setattr(settings, "VK_SECRET", "")
    assert _vk_secret_ok({}) is False


def test_vk_webhook_secret_match(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(settings, "VK_SECRET", "expected")
    assert _vk_secret_ok({"secret": "expected"}) is True
    assert _vk_secret_ok({"secret": "bad"}) is False


def test_vk_callback_group_matches_configured_community(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(settings, "VK_GROUP_ID", "238191212")

    assert _vk_group_ok({"group_id": 238191212}) is True
    assert _vk_group_ok({"group_id": "238191212"}) is True
    assert _vk_group_ok({"group_id": 1}) is False
    assert _vk_group_ok({}) is False


def test_vk_callback_group_is_fail_closed_without_production_group(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("ALLOW_INSECURE_MESSENGER_WEBHOOKS", "1")
    monkeypatch.setattr(settings, "VK_GROUP_ID", "")

    assert _vk_group_ok({"group_id": 238191212}) is False
