from __future__ import annotations

import pytest

from services.validators.architecture import validate_public_payment_base_url
from services.validators.base import ValidationError


def _clear_payment_env(monkeypatch):
    for name in (
        "PAYMENT_PUBLIC_URL_REQUIRED",
        "PAYMENT_PUBLIC_BASE_URL",
        "MESSENGER_PUBLIC_BASE_URL",
        "PUBLIC_BASE_URL",
        "TELEGRAM_WEBHOOK_PUBLIC_BASE_URL",
        "PAYMENT_HTTP_ENABLED",
        "MESSENGER_WEBHOOK_ENABLED",
        "VALIDATOR_RELEASE_MODE",
        "APP_ENV",
    ):
        monkeypatch.delenv(name, raising=False)


def test_payment_base_url_required_when_explicit(monkeypatch):
    _clear_payment_env(monkeypatch)
    monkeypatch.setenv("PAYMENT_PUBLIC_URL_REQUIRED", "1")

    with pytest.raises(ValidationError):
        validate_public_payment_base_url(strict=True)


def test_payment_base_url_required_for_enabled_payment_ingress_in_prod(monkeypatch):
    _clear_payment_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("PAYMENT_HTTP_ENABLED", "1")

    with pytest.raises(ValidationError, match="Public payment base URL is required"):
        validate_public_payment_base_url(strict=True)


def test_disabled_payment_ingress_does_not_inherit_legacy_master(monkeypatch):
    _clear_payment_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("MESSENGER_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("PAYMENT_HTTP_ENABLED", "0")

    validate_public_payment_base_url(strict=True)


def test_payment_base_url_must_be_tls(monkeypatch):
    _clear_payment_env(monkeypatch)
    monkeypatch.setenv("PAYMENT_PUBLIC_URL_REQUIRED", "1")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "not-tls://example.test")

    with pytest.raises(ValidationError):
        validate_public_payment_base_url(strict=True)


def test_payment_base_url_accepts_tls(monkeypatch):
    _clear_payment_env(monkeypatch)
    monkeypatch.setenv("PAYMENT_PUBLIC_URL_REQUIRED", "1")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://example.test")

    validate_public_payment_base_url(strict=True)


def test_payment_base_url_skips_release_mode_without_explicit_requirement(monkeypatch):
    _clear_payment_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("PAYMENT_HTTP_ENABLED", "1")
    monkeypatch.setenv("VALIDATOR_RELEASE_MODE", "1")

    validate_public_payment_base_url(strict=True)
