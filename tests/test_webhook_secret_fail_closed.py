from __future__ import annotations

from config.settings import settings
from runtime.messenger_ingress import _max_secret_ok, _vk_secret_ok
from runtime.telegram_webhook_runtime import telegram_secret_ok


class DummyRequest:
    def __init__(self, *, headers: dict[str, str] | None = None, query: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.query = query or {}


def test_telegram_webhook_secret_missing_is_fail_closed(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'prod')
    monkeypatch.delenv('ALLOW_INSECURE_TELEGRAM_WEBHOOK', raising=False)
    monkeypatch.setattr(settings, 'TELEGRAM_WEBHOOK_SECRET_TOKEN', '')
    assert telegram_secret_ok(DummyRequest()) is False


def test_telegram_webhook_secret_dev_escape_hatch(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'dev')
    monkeypatch.setenv('ALLOW_INSECURE_TELEGRAM_WEBHOOK', '1')
    monkeypatch.setattr(settings, 'TELEGRAM_WEBHOOK_SECRET_TOKEN', '')
    assert telegram_secret_ok(DummyRequest()) is True


def test_telegram_webhook_secret_uses_constant_time_compare(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'prod')
    monkeypatch.setattr(settings, 'TELEGRAM_WEBHOOK_SECRET_TOKEN', 'expected')
    assert telegram_secret_ok(DummyRequest(headers={'X-Telegram-Bot-Api-Secret-Token': 'expected'})) is True
    assert telegram_secret_ok(DummyRequest(headers={'X-Telegram-Bot-Api-Secret-Token': 'bad'})) is False


def test_max_webhook_secret_missing_is_fail_closed(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'prod')
    monkeypatch.delenv('ALLOW_INSECURE_MESSENGER_WEBHOOKS', raising=False)
    monkeypatch.setattr(settings, 'MAX_WEBHOOK_SECRET', '')
    assert _max_secret_ok(DummyRequest(), {}) is False


def test_max_webhook_secret_dev_escape_hatch(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'dev')
    monkeypatch.setenv('ALLOW_INSECURE_MESSENGER_WEBHOOKS', '1')
    monkeypatch.setattr(settings, 'MAX_WEBHOOK_SECRET', '')
    assert _max_secret_ok(DummyRequest(), {}) is True


def test_max_webhook_secret_match(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'prod')
    monkeypatch.setattr(settings, 'MAX_WEBHOOK_SECRET', 'expected')
    assert _max_secret_ok(DummyRequest(headers={'X-Max-Webhook-Secret': 'expected'}), {}) is True
    assert _max_secret_ok(DummyRequest(headers={'X-Max-Webhook-Secret': 'bad'}), {}) is False


def test_vk_webhook_secret_missing_is_fail_closed(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'prod')
    monkeypatch.delenv('ALLOW_INSECURE_MESSENGER_WEBHOOKS', raising=False)
    monkeypatch.setattr(settings, 'VK_SECRET', '')
    assert _vk_secret_ok({}) is False


def test_vk_webhook_secret_match(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'prod')
    monkeypatch.setattr(settings, 'VK_SECRET', 'expected')
    assert _vk_secret_ok({'secret': 'expected'}) is True
    assert _vk_secret_ok({'secret': 'bad'}) is False
