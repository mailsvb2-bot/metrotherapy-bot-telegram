from __future__ import annotations

from services.messenger import preflight


def _configure_common(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(preflight.settings, "MESSENGER_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(preflight.settings, "MESSENGER_PUBLIC_BASE_URL", "https://bot.example.com")


def test_vk_secret_is_required_in_deployed_webhook_mode(monkeypatch) -> None:
    _configure_common(monkeypatch)
    monkeypatch.setattr(preflight.settings, "VK_GROUP_TOKEN", "vk-token")
    monkeypatch.setattr(preflight.settings, "VK_CONFIRMATION_TOKEN", "confirm")
    monkeypatch.setattr(preflight.settings, "VK_GROUP_ID", "123")
    monkeypatch.setattr(preflight.settings, "VK_SECRET", "")

    status = preflight.check_vk_preflight()

    assert not status.ok
    assert "VK_SECRET" in status.missing


def test_max_secret_is_required_in_deployed_webhook_mode(monkeypatch) -> None:
    _configure_common(monkeypatch)
    monkeypatch.setattr(preflight.settings, "MAX_BOT_TOKEN", "max-token")
    monkeypatch.setattr(preflight.settings, "MAX_BOT_LINK_BASE", "https://max.example/{payload}")
    monkeypatch.setattr(preflight.settings, "MAX_WEBHOOK_SECRET", "")

    status = preflight.check_max_preflight()

    assert not status.ok
    assert "MAX_WEBHOOK_SECRET" in status.missing


def test_max_vk_preflight_passes_when_deployed_secrets_are_present(monkeypatch) -> None:
    _configure_common(monkeypatch)
    monkeypatch.setattr(preflight.settings, "VK_GROUP_TOKEN", "vk-token")
    monkeypatch.setattr(preflight.settings, "VK_CONFIRMATION_TOKEN", "confirm")
    monkeypatch.setattr(preflight.settings, "VK_GROUP_ID", "123")
    monkeypatch.setattr(preflight.settings, "VK_SECRET", "secret")
    monkeypatch.setattr(preflight.settings, "MAX_BOT_TOKEN", "max-token")
    monkeypatch.setattr(preflight.settings, "MAX_BOT_LINK_BASE", "https://max.example/{payload}")
    monkeypatch.setattr(preflight.settings, "MAX_WEBHOOK_SECRET", "secret")

    assert preflight.check_vk_preflight().ok
    assert preflight.check_max_preflight().ok
