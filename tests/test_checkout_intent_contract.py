from __future__ import annotations

import pytest

from services.payments.checkout_intent import (
    CheckoutIntentError,
    add_checkout_intent_to_url,
    checkout_intent_required,
    sign_checkout_intent,
    verify_checkout_intent,
)


def test_checkout_intent_roundtrip(monkeypatch):
    monkeypatch.delenv("PAYMENT_CHECKOUT_SIGNING_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    token = sign_checkout_intent(user_id=123, package_id="p10", source="telegram", ttl_sec=600)

    payload = verify_checkout_intent(
        token,
        expected_user_id=123,
        expected_package_id="p10",
        expected_kind="tokens",
        expected_source="telegram",
    )

    assert payload["sub"] == "123"
    assert payload["package_id"] == "p10"
    assert payload["kind"] == "tokens"
    assert payload["source"] == "telegram"
    assert payload["v"] == 2


def test_checkout_intent_rejects_package_mismatch(monkeypatch):
    monkeypatch.delenv("PAYMENT_CHECKOUT_SIGNING_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    token = sign_checkout_intent(user_id=123, package_id="p10", source="telegram", ttl_sec=600)

    with pytest.raises(CheckoutIntentError, match="package_id_mismatch"):
        verify_checkout_intent(
            token,
            expected_user_id=123,
            expected_package_id="p20",
            expected_kind="tokens",
        )


def test_checkout_intent_rejects_expired_token(monkeypatch):
    monkeypatch.delenv("PAYMENT_CHECKOUT_SIGNING_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    token = sign_checkout_intent(user_id=123, package_id="p10", source="telegram", ttl_sec=60)

    with pytest.raises(CheckoutIntentError, match="expired"):
        verify_checkout_intent(
            token,
            expected_user_id=123,
            expected_package_id="p10",
            expected_kind="tokens",
            now=9999999999,
        )


def test_add_checkout_intent_to_url_preserves_payment_params(monkeypatch):
    monkeypatch.delenv("PAYMENT_CHECKOUT_SIGNING_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    url = add_checkout_intent_to_url(
        "https://metrotherapy.ru/pay/yookassa?source=telegram&user_id=123&kind=tokens&package_id=p10",
        user_id=123,
        package_id="p10",
        source="telegram",
    )

    assert "source=telegram" in url
    assert "user_id=123" in url
    assert "package_id=p10" in url
    assert "intent=" in url


def test_checkout_intent_required_defaults_to_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("PAYMENT_CHECKOUT_INTENT_REQUIRED", raising=False)
    monkeypatch.delenv("ALLOW_UNSIGNED_PAYMENT_CHECKOUT_IN_PROD", raising=False)

    assert checkout_intent_required() is True


def test_checkout_intent_cannot_be_disabled_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("PAYMENT_CHECKOUT_INTENT_REQUIRED", "0")
    monkeypatch.setenv("ALLOW_UNSIGNED_PAYMENT_CHECKOUT_IN_PROD", "1")
    monkeypatch.setenv("PAYMENT_DANGEROUS_OVERRIDES_ALLOWED", "1")

    assert checkout_intent_required() is True


def test_checkout_intent_rejects_source_mismatch(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    token = sign_checkout_intent(
        user_id=123, package_id="practice_start_7", source="vk", ttl_sec=600
    )
    with pytest.raises(CheckoutIntentError, match="source_mismatch"):
        verify_checkout_intent(
            token, expected_user_id=123, expected_package_id="practice_start_7",
            expected_source="max", expected_amount_minor=190000, expected_currency="RUB",
        )


def test_checkout_intent_rejects_price_mismatch(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    token = sign_checkout_intent(
        user_id=123, package_id="practice_start_7", source="vk", ttl_sec=600
    )
    with pytest.raises(CheckoutIntentError, match="amount_minor_mismatch"):
        verify_checkout_intent(
            token, expected_user_id=123, expected_package_id="practice_start_7",
            expected_source="vk", expected_amount_minor=1, expected_currency="RUB",
        )
