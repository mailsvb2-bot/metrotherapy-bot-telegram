from __future__ import annotations

from services import visual_creative_capability as capability


def _clear(monkeypatch):
    for name in (
        "APP_ENV",
        "VISUAL_CREATIVE_ENABLED",
        "VISUAL_CREATIVE_STUDIO_ENABLED",
        "VISUAL_GATEWAY_URL",
        "VISUAL_GATEWAY_TOKEN",
        "VISUAL_DEPLOYMENT_COUNTRY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_studio_is_disabled_by_default_even_when_base_capability_is_legacy_enabled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "secret")
    monkeypatch.setenv("VISUAL_DEPLOYMENT_COUNTRY", "RU")
    assert capability.visual_creative_enabled() is True
    assert capability.visual_creative_studio_enabled() is False
    snapshot = capability.visual_creative_configuration_snapshot(app_env="prod")
    assert snapshot["visual_creative_ready"] is True
    assert snapshot["visual_creative_studio_enabled"] is False


def test_studio_requires_base_visual_capability(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "0")
    monkeypatch.setenv("VISUAL_CREATIVE_STUDIO_ENABLED", "1")
    assert capability.visual_creative_studio_enabled() is False
    snapshot = capability.visual_creative_configuration_snapshot(app_env="prod")
    assert snapshot["visual_creative_ready"] is False
    assert "studio_requires_visual_creative" in snapshot["visual_creative_configuration_errors"]


def test_invalid_studio_flag_fails_closed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_CREATIVE_STUDIO_ENABLED", "sometimes")
    snapshot = capability.visual_creative_configuration_snapshot(app_env="prod")
    assert snapshot["visual_creative_ready"] is False
    assert "invalid_studio_enabled_flag" in snapshot["visual_creative_configuration_errors"]


def test_studio_enabled_with_complete_base_configuration(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "1")
    monkeypatch.setenv("VISUAL_CREATIVE_STUDIO_ENABLED", "1")
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "secret")
    monkeypatch.setenv("VISUAL_DEPLOYMENT_COUNTRY", "RU")
    assert capability.visual_creative_studio_enabled() is True
    snapshot = capability.visual_creative_configuration_snapshot(app_env="prod")
    assert snapshot["visual_creative_ready"] is True
    assert snapshot["visual_creative_studio_enabled"] is True
