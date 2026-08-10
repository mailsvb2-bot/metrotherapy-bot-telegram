from __future__ import annotations

from services import visual_creative_capability as capability


def _clear(monkeypatch) -> None:
    for name in (
        "APP_ENV",
        "VISUAL_CREATIVE_ENABLED",
        "VISUAL_GATEWAY_URL",
        "VISUAL_GATEWAY_TOKEN",
        "VISUAL_GATEWAY_ALLOW_INSECURE_HTTP",
        "VISUAL_DEPLOYMENT_COUNTRY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_explicitly_disabled_capability_is_ready_without_gateway(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "0")

    snapshot = capability.visual_creative_configuration_snapshot(app_env="prod")

    assert snapshot["visual_creative_enabled"] is False
    assert snapshot["visual_creative_ready"] is True
    assert snapshot["visual_creative_activation_mode"] == "disabled"
    assert snapshot["visual_creative_configuration_errors"] == []


def test_unset_flag_preserves_legacy_implicit_activation(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "secret")
    monkeypatch.setenv("VISUAL_DEPLOYMENT_COUNTRY", "ru")

    snapshot = capability.visual_creative_configuration_snapshot(app_env="prod")

    assert capability.visual_creative_enabled() is True
    assert capability.visual_creative_country_code() == "RU"
    assert snapshot["visual_creative_activation_mode"] == "implicit"
    assert snapshot["visual_creative_ready"] is True


def test_enabled_capability_requires_url_token_and_country(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "1")

    snapshot = capability.visual_creative_configuration_snapshot(app_env="prod")

    assert snapshot["visual_creative_enabled"] is True
    assert snapshot["visual_creative_ready"] is False
    assert set(snapshot["visual_creative_configuration_errors"]) == {
        "gateway_url",
        "gateway_token",
        "deployment_country",
        "secure_transport",
    }


def test_enabled_https_configuration_is_ready(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "yes")
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal/prefix")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "secret")
    monkeypatch.setenv("VISUAL_DEPLOYMENT_COUNTRY", "NL")

    snapshot = capability.visual_creative_configuration_snapshot(app_env="production")

    assert snapshot["visual_creative_ready"] is True
    assert snapshot["visual_creative_gateway_secure_transport"] is True
    assert snapshot["visual_creative_country_configured"] is True


def test_production_insecure_gateway_is_not_ready_even_with_override(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "1")
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "http://gateway.internal:8097")
    monkeypatch.setenv("VISUAL_GATEWAY_ALLOW_INSECURE_HTTP", "1")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "secret")
    monkeypatch.setenv("VISUAL_DEPLOYMENT_COUNTRY", "RU")

    production = capability.visual_creative_configuration_snapshot(app_env="prod")
    development = capability.visual_creative_configuration_snapshot(app_env="dev")

    assert production["visual_creative_ready"] is False
    assert production["visual_creative_configuration_errors"] == ["secure_transport"]
    assert development["visual_creative_ready"] is True


def test_invalid_enabled_flag_fails_closed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "maybe")

    snapshot = capability.visual_creative_configuration_snapshot(app_env="dev")

    assert capability.visual_creative_enabled() is False
    assert snapshot["visual_creative_activation_mode"] == "invalid"
    assert snapshot["visual_creative_ready"] is False
    assert snapshot["visual_creative_configuration_errors"] == ["invalid_enabled_flag"]


def test_country_must_be_two_letters(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISUAL_DEPLOYMENT_COUNTRY", "RUS")
    assert capability.visual_creative_country_code() == ""
