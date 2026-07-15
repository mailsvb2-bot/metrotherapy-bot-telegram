from __future__ import annotations

import pytest

from services.validators.base import ValidationError
from services.validators.prod import validate_prod_monetization_contract


def _base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TOKEN_ECONOMY_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "hard")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "billing@example.test")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")


def test_prod_accepts_buyer_parity_stars_pricing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch)
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "buyer_parity")
    monkeypatch.setenv("TELEGRAM_STARS_BUYER_RUB_PER_XTR", "1.54905")

    validate_prod_monetization_contract(strict=True)


def test_prod_rejects_invalid_stars_pricing_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch)
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "one_star_one_ruble")

    with pytest.raises(ValidationError, match="TELEGRAM_STARS_PRICING_MODE"):
        validate_prod_monetization_contract(strict=True)


def test_prod_rejects_non_positive_buyer_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch)
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "buyer_parity")
    monkeypatch.setenv("TELEGRAM_STARS_BUYER_RUB_PER_XTR", "0")

    with pytest.raises(ValidationError, match="TELEGRAM_STARS_BUYER_RUB_PER_XTR"):
        validate_prod_monetization_contract(strict=True)
