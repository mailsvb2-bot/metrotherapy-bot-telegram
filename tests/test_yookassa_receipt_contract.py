from __future__ import annotations

import pytest

from services.payments import yookassa_checkout


def test_receipt_uses_configured_tax_system_and_canonical_payment_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "receipts@example.com")
    monkeypatch.setenv("YOOKASSA_TAX_SYSTEM_CODE", "4")
    monkeypatch.setenv("YOOKASSA_VAT_CODE", "2")
    monkeypatch.delenv("YOOKASSA_PAYMENT_MODE", raising=False)
    monkeypatch.setenv("YOOKASSA_PAYMENT_SUBJECT", "service")

    receipt = yookassa_checkout.build_yookassa_receipt(
        amount_value="1900.00",
        description="Metrotherapy package",
    )

    assert receipt["tax_system_code"] == 4
    assert receipt["customer"]["email"] == "receipts@example.com"
    assert receipt["items"][0]["vat_code"] == 2
    assert receipt["items"][0]["payment_mode"] == "full_payment"
    assert receipt["items"][0]["payment_subject"] == "service"


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("YOOKASSA_TAX_SYSTEM_CODE", "0"),
        ("YOOKASSA_TAX_SYSTEM_CODE", "7"),
        ("YOOKASSA_TAX_SYSTEM_CODE", "bad"),
        ("YOOKASSA_VAT_CODE", "0"),
        ("YOOKASSA_VAT_CODE", "7"),
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
