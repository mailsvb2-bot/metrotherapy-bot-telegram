from __future__ import annotations

import pytest

from services.validators.base import ValidationError
from services.validators.visual_creative import validate_prod_visual_creative_contract


def _clear(monkeypatch) -> None:
    for name in (
        "APP_ENV",
        "VISUAL_CREATIVE_ENABLED",
        "VISUAL_CREATIVE_STUDIO_ENABLED",
        "VISUAL_GATEWAY_URL",
        "VISUAL_GATEWAY_TOKEN",
        "VISUAL_GATEWAY_ALLOW_INSECURE_HTTP",
        "VISUAL_DEPLOYMENT_COUNTRY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_non_production_does_not_gate_visual_creative(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "maybe")
    validate_prod_visual_creative_contract()


def test_disabled_production_capability_passes(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "0")
    validate_prod_visual_creative_contract()


def test_complete_production_configuration_passes(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "1")
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal/prefix")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "secret")
    monkeypatch.setenv("VISUAL_DEPLOYMENT_COUNTRY", "RU")
    validate_prod_visual_creative_contract()


def test_enabled_incomplete_production_configuration_fails(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "1")

    with pytest.raises(ValidationError) as exc_info:
        validate_prod_visual_creative_contract()

    message = str(exc_info.value)
    assert "gateway_url" in message
    assert "gateway_token" in message
    assert "deployment_country" in message
    assert "secure_transport" in message


def test_insecure_production_gateway_fails_even_with_transport_override(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "1")
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "http://gateway.internal:8097")
    monkeypatch.setenv("VISUAL_GATEWAY_ALLOW_INSECURE_HTTP", "1")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "secret")
    monkeypatch.setenv("VISUAL_DEPLOYMENT_COUNTRY", "NL")

    with pytest.raises(ValidationError, match="secure_transport"):
        validate_prod_visual_creative_contract()


def test_invalid_enabled_flag_fails_closed_in_production(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "sometimes")

    with pytest.raises(ValidationError, match="invalid_enabled_flag"):
        validate_prod_visual_creative_contract()


def test_studio_requires_base_capability_in_production(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "0")
    monkeypatch.setenv("VISUAL_CREATIVE_STUDIO_ENABLED", "1")

    with pytest.raises(ValidationError, match="studio_requires_visual_creative"):
        validate_prod_visual_creative_contract()


def test_invalid_studio_flag_fails_closed_in_production(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("VISUAL_CREATIVE_STUDIO_ENABLED", "sometimes")

    with pytest.raises(ValidationError, match="invalid_studio_enabled_flag"):
        validate_prod_visual_creative_contract()


def test_strict_false_reports_no_exception(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "1")
    validate_prod_visual_creative_contract(strict=False)


def test_validator_error_does_not_expose_gateway_token(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("VISUAL_CREATIVE_ENABLED", "1")
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "top-secret-token")

    with pytest.raises(ValidationError) as exc_info:
        validate_prod_visual_creative_contract()

    assert "top-secret-token" not in str(exc_info.value)
