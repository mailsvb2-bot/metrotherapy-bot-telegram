from __future__ import annotations

import json

import pytest

from services.payments import common, yookassa_checkout
from services.payments.receipt_contract import (
    PAYMENT_MODES,
    PAYMENT_SUBJECTS,
    VAT_CODES,
    validate_receipt_contract,
)
from services.validators.base import ValidationError
from services.validators.prod import validate_prod_monetization_contract


def test_receipt_uses_configured_tax_system_and_canonical_payment_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "receipts@example.com")
    monkeypatch.setenv("YOOKASSA_TAX_SYSTEM_CODE", "4")
    monkeypatch.setenv("YOOKASSA_VAT_CODE", "12")
    monkeypatch.delenv("YOOKASSA_PAYMENT_MODE", raising=False)
    monkeypatch.setenv("YOOKASSA_PAYMENT_SUBJECT", "service")

    receipt = yookassa_checkout.build_yookassa_receipt(
        amount_value="1900.00",
        description="Metrotherapy package",
    )

    assert receipt["tax_system_code"] == 4
    assert receipt["customer"]["email"] == "receipts@example.com"
    assert receipt["items"][0]["vat_code"] == 12
    assert receipt["items"][0]["payment_mode"] == "full_payment"
    assert receipt["items"][0]["payment_subject"] == "service"


@pytest.mark.parametrize("vat_code", (1, 6, 7, 10, 11, 12))
def test_current_yookassa_vat_codes_are_supported(monkeypatch, vat_code):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "receipts@example.com")
    monkeypatch.setenv("YOOKASSA_TAX_SYSTEM_CODE", "2")
    monkeypatch.setenv("YOOKASSA_VAT_CODE", str(vat_code))
    monkeypatch.setenv("YOOKASSA_PAYMENT_MODE", "full_payment")
    monkeypatch.setenv("YOOKASSA_PAYMENT_SUBJECT", "service")

    receipt = yookassa_checkout.build_yookassa_receipt(
        amount_value="1900.00",
        description="Metrotherapy package",
    )

    assert receipt["items"][0]["vat_code"] == vat_code
    assert vat_code in VAT_CODES


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("YOOKASSA_TAX_SYSTEM_CODE", "0"),
        ("YOOKASSA_TAX_SYSTEM_CODE", "7"),
        ("YOOKASSA_TAX_SYSTEM_CODE", "bad"),
        ("YOOKASSA_VAT_CODE", "0"),
        ("YOOKASSA_VAT_CODE", "13"),
        ("YOOKASSA_VAT_CODE", "bad"),
    ),
)
def test_receipt_rejects_invalid_fiscal_integer(monkeypatch, name, value):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "receipts@example.com")
    monkeypatch.setenv("YOOKASSA_TAX_SYSTEM_CODE", "2")
    monkeypatch.setenv("YOOKASSA_VAT_CODE", "1")
    monkeypatch.setenv(name, value)

    with pytest.raises(yookassa_checkout.YooKassaCheckoutError):
        yookassa_checkout.build_yookassa_receipt(
            amount_value="1900.00",
            description="Metrotherapy package",
        )


@pytest.mark.parametrize("payment_mode", ("full_payment", "full_prepayment"))
def test_supported_payment_modes_are_emitted(monkeypatch, payment_mode):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "receipts@example.com")
    monkeypatch.setenv("YOOKASSA_PAYMENT_MODE", payment_mode)

    receipt = yookassa_checkout.build_yookassa_receipt(
        amount_value="1900.00",
        description="Metrotherapy package",
    )

    assert receipt["items"][0]["payment_mode"] == payment_mode
    assert payment_mode in PAYMENT_MODES


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("YOOKASSA_PAYMENT_MODE", "partial_payment"),
        ("YOOKASSA_PAYMENT_MODE", "anything"),
        ("YOOKASSA_PAYMENT_SUBJECT", "subscription"),
        ("YOOKASSA_PAYMENT_SUBJECT", "anything"),
    ),
)
def test_direct_receipt_rejects_invalid_fiscal_enum(monkeypatch, name, value):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "receipts@example.com")
    monkeypatch.setenv("YOOKASSA_PAYMENT_MODE", "full_payment")
    monkeypatch.setenv("YOOKASSA_PAYMENT_SUBJECT", "service")
    monkeypatch.setenv(name, value)

    with pytest.raises(yookassa_checkout.YooKassaCheckoutError):
        yookassa_checkout.build_yookassa_receipt(
            amount_value="1900.00",
            description="Metrotherapy package",
        )


def test_legacy_provider_data_uses_same_fiscal_contract(monkeypatch):
    monkeypatch.setattr(common.settings, "YOOKASSA_TAX_SYSTEM_CODE", 3)
    monkeypatch.setattr(common.settings, "YOOKASSA_VAT_CODE", 11)
    monkeypatch.setattr(common.settings, "YOOKASSA_PAYMENT_MODE", "full_prepayment")
    monkeypatch.setattr(common.settings, "YOOKASSA_PAYMENT_SUBJECT", "service")

    provider_data = json.loads(common.yookassa_provider_data_receipt("Metrotherapy", 1900))
    receipt = provider_data["receipt"]

    assert receipt["tax_system_code"] == 3
    assert receipt["items"][0]["vat_code"] == 11
    assert receipt["items"][0]["payment_mode"] == "full_prepayment"
    assert receipt["items"][0]["payment_subject"] == "service"


def test_legacy_provider_data_rejects_invalid_settings(monkeypatch):
    monkeypatch.setattr(common.settings, "YOOKASSA_PAYMENT_MODE", "partial_payment")

    with pytest.raises(ValueError, match="YOOKASSA_PAYMENT_MODE"):
        common.yookassa_provider_data_receipt("Metrotherapy", 1900)


def _prod_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TOKEN_ECONOMY_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "hard")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "receipts@example.com")
    monkeypatch.setenv("YOOKASSA_TAX_SYSTEM_CODE", "2")
    monkeypatch.setenv("YOOKASSA_VAT_CODE", "1")
    monkeypatch.setenv("YOOKASSA_PAYMENT_MODE", "full_payment")
    monkeypatch.setenv("YOOKASSA_PAYMENT_SUBJECT", "service")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("YOOKASSA_VAT_CODE", "13"),
        ("YOOKASSA_PAYMENT_MODE", "partial_payment"),
        ("YOOKASSA_PAYMENT_SUBJECT", "subscription"),
    ),
)
def test_production_monetization_rejects_invalid_fiscal_contract(monkeypatch, name, value):
    _prod_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=name):
        validate_prod_monetization_contract(strict=True)


def test_contract_contains_current_service_values():
    assert VAT_CODES == frozenset(range(1, 13))
    assert PAYMENT_MODES == frozenset({"full_prepayment", "full_payment"})
    assert "service" in PAYMENT_SUBJECTS
    assert validate_receipt_contract(
        tax_system_code="2",
        vat_code="12",
        payment_mode="full_payment",
        payment_subject="service",
    ) == (2, 12, "full_payment", "service")
