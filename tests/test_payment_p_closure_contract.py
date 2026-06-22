from __future__ import annotations

from runtime import payment_http
from services.payments.public_url import payment_public_base_url
from services.practice_tokens import enforcement_mode


def test_prod_token_enforcement_defaults_to_soft(monkeypatch):
    monkeypatch.delenv("TOKEN_ENFORCEMENT_MODE", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")

    assert enforcement_mode() == "soft"


def test_dev_token_enforcement_defaults_to_off(monkeypatch):
    monkeypatch.delenv("TOKEN_ENFORCEMENT_MODE", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")

    assert enforcement_mode() == "off"


def test_explicit_token_enforcement_modes_still_win(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "hard")
    assert enforcement_mode() == "hard"

    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "off")
    assert enforcement_mode() == "off"


def test_legacy_public_payment_kinds_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_LEGACY_PUBLIC_PAYMENT_KINDS", raising=False)

    assert payment_http._legacy_kind_error_response("subscription") is not None
    assert payment_http._legacy_kind_error_response("gift") is not None
    assert payment_http._legacy_kind_error_response("tokens") is None


def test_legacy_public_payment_kinds_can_be_enabled_explicitly(monkeypatch):
    monkeypatch.setenv("ENABLE_LEGACY_PUBLIC_PAYMENT_KINDS", "1")

    assert payment_http._legacy_kind_error_response("subscription") is None
    assert payment_http._legacy_kind_error_response("gift") is None


def test_payment_kind_normalization_prefers_tokens_and_package_links():
    assert payment_http._normalize_payment_kind(None, "") == "tokens"
    assert payment_http._normalize_payment_kind("unknown", "") == "tokens"
    assert payment_http._normalize_payment_kind("subscription", "practice_start_7") == "tokens"
    assert payment_http._normalize_payment_kind("gift", "practice_start_7") == "tokens"
    assert payment_http._normalize_payment_kind("tokens", "practice_60") == "tokens"


def test_shared_payment_public_base_url_precedence(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://messenger.example/")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://payment.example")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://public.example")

    assert payment_public_base_url() == "https://messenger.example"
