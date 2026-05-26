from __future__ import annotations

from services.messenger import preflight


def test_telegram_preflight_accepts_polling_with_bot_token(monkeypatch):
    monkeypatch.setattr(preflight.settings, "BOT_TOKEN", "000000:TEST", raising=False)
    monkeypatch.setattr(preflight.settings, "TELEGRAM_TRANSPORT", "polling", raising=False)
    monkeypatch.setattr(preflight.settings, "TELEGRAM_WEBHOOK_ENABLED", False, raising=False)

    status = preflight.check_telegram_preflight()

    assert status.ok is True
    assert status.channel == "telegram"
    assert status.details == {"transport": "polling", "webhook_enabled": False}


def test_telegram_preflight_requires_webhook_secret_for_webhook(monkeypatch):
    monkeypatch.setattr(preflight.settings, "BOT_TOKEN", "000000:TEST", raising=False)
    monkeypatch.setattr(preflight.settings, "TELEGRAM_TRANSPORT", "webhook", raising=False)
    monkeypatch.setattr(preflight.settings, "TELEGRAM_WEBHOOK_ENABLED", True, raising=False)
    monkeypatch.setattr(preflight.settings, "TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", "https://example.test", raising=False)
    monkeypatch.setattr(preflight.settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "", raising=False)

    status = preflight.check_telegram_preflight()

    assert status.ok is False
    assert "TELEGRAM_WEBHOOK_SECRET_TOKEN" in status.missing


def test_vk_preflight_warns_when_secret_missing_for_webhook(monkeypatch):
    monkeypatch.setattr(preflight.settings, "MESSENGER_WEBHOOK_ENABLED", True, raising=False)
    monkeypatch.setattr(preflight.settings, "MESSENGER_PUBLIC_BASE_URL", "https://bot.example.test", raising=False)
    monkeypatch.setattr(preflight.settings, "VK_GROUP_TOKEN", "group-token", raising=False)
    monkeypatch.setattr(preflight.settings, "VK_CONFIRMATION_TOKEN", "confirm", raising=False)
    monkeypatch.setattr(preflight.settings, "VK_GROUP_ID", "123", raising=False)
    monkeypatch.setattr(preflight.settings, "VK_SECRET", "", raising=False)

    status = preflight.check_vk_preflight()

    assert status.ok is True
    assert status.missing == ()
    assert status.warnings == ("VK_SECRET is not configured; VK webhook secret verification is not enforced",)
    assert status.details == {"webhook_url": "https://bot.example.test/webhooks/vk"}


def test_max_preflight_requires_public_base_when_webhook_enabled(monkeypatch):
    monkeypatch.setattr(preflight.settings, "MESSENGER_WEBHOOK_ENABLED", True, raising=False)
    monkeypatch.setattr(preflight.settings, "MESSENGER_PUBLIC_BASE_URL", "", raising=False)
    monkeypatch.setattr(preflight.settings, "MAX_BOT_TOKEN", "max-token", raising=False)
    monkeypatch.setattr(preflight.settings, "MAX_BOT_LINK_BASE", "https://max.ru/bot", raising=False)
    monkeypatch.setattr(preflight.settings, "MAX_API_BASE_URL", "https://platform-api.max.ru", raising=False)

    status = preflight.check_max_preflight()

    assert status.ok is False
    assert "MESSENGER_PUBLIC_BASE_URL" in status.missing
