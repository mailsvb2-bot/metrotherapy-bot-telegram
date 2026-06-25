from __future__ import annotations

from runtime import health_server
from services.messenger.preflight import MessengerPreflightStatus


def test_messenger_readiness_fails_when_enabled_channel_preflight_is_red(monkeypatch) -> None:
    monkeypatch.setattr(health_server.settings, "MESSENGER_WEBHOOK_ENABLED", True)

    def fake_preflights():
        return (
            MessengerPreflightStatus(channel="telegram", ok=True),
            MessengerPreflightStatus(channel="max", ok=False, missing=("MAX_WEBHOOK_SECRET",)),
            MessengerPreflightStatus(channel="vk", ok=True),
        )

    monkeypatch.setattr(health_server, "check_all_preflights", fake_preflights)

    ok, errors, fields = health_server._messenger_preflight_readiness()

    assert ok is False
    assert errors
    assert fields["max_preflight_ok"] is False


def test_messenger_readiness_is_green_when_enabled_preflights_are_green(monkeypatch) -> None:
    monkeypatch.setattr(health_server.settings, "MESSENGER_WEBHOOK_ENABLED", True)

    def fake_preflights():
        return (
            MessengerPreflightStatus(channel="telegram", ok=True),
            MessengerPreflightStatus(channel="max", ok=True),
            MessengerPreflightStatus(channel="vk", ok=True),
        )

    monkeypatch.setattr(health_server, "check_all_preflights", fake_preflights)

    ok, errors, fields = health_server._messenger_preflight_readiness()

    assert ok is True
    assert errors == []
    assert fields["max_preflight_ok"] is True
    assert fields["vk_preflight_ok"] is True
