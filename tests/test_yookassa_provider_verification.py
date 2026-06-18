from __future__ import annotations

import pytest

from services.payments.yookassa_provider import (
    YooKassaProviderVerificationError,
    provider_verification_required,
    verify_yookassa_webhook_with_provider,
)


def _payload(amount: str = "1900.00") -> dict:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": "payment_fixture_1",
            "status": "succeeded",
            "amount": {"value": amount, "currency": "RUB"},
            "metadata": {
                "external_user_id": "123",
                "user_id": "123",
                "kind": "tokens",
                "package_id": "p10",
                "gift_token": "",
            },
        },
    }


def test_provider_verification_required_defaults_to_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", raising=False)
    monkeypatch.delenv("ALLOW_UNVERIFIED_YOOKASSA_WEBHOOK_IN_PROD", raising=False)

    assert provider_verification_required() is True


def test_provider_verification_compares_provider_payload(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", "1")

    from services.payments import yookassa_provider

    monkeypatch.setattr(
        yookassa_provider,
        "fetch_yookassa_payment",
        lambda payment_id: {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": "1900.00", "currency": "RUB"},
            "metadata": {
                "external_user_id": "123",
                "user_id": "123",
                "kind": "tokens",
                "package_id": "p10",
                "gift_token": "",
            },
        },
    )

    verify_yookassa_webhook_with_provider(_payload())


def test_provider_verification_rejects_amount_mismatch(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", "1")

    from services.payments import yookassa_provider

    monkeypatch.setattr(
        yookassa_provider,
        "fetch_yookassa_payment",
        lambda payment_id: {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": "2000.00", "currency": "RUB"},
            "metadata": {
                "external_user_id": "123",
                "user_id": "123",
                "kind": "tokens",
                "package_id": "p10",
                "gift_token": "",
            },
        },
    )

    with pytest.raises(YooKassaProviderVerificationError, match="amount_mismatch"):
        verify_yookassa_webhook_with_provider(_payload())
