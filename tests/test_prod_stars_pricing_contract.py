from __future__ import annotations

from pathlib import Path

import pytest

from services.validators.base import ValidationError
from services.validators.prod import validate_prod_monetization_contract


EXPLICIT_PRICES = {
    "TELEGRAM_STARS_PRICE_PRACTICE_START_7": "1500",
    "TELEGRAM_STARS_PRICE_PRACTICE_60": "2500",
    "TELEGRAM_STARS_PRICE_PRACTICE_ANTISTRESS_60": "5000",
    "TELEGRAM_STARS_PRICE_PRACTICE_PERSONAL_MONTH": "15000",
}


def _base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    marker = tmp_path / "telegram-stars-only-checkout-v1.applied"
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TOKEN_ECONOMY_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "hard")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "billing@example.test")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")
    monkeypatch.setenv("TELEGRAM_STARS_ONLY_MIGRATION_MARKER", str(marker))
    return marker


def test_prod_accepts_explicit_stars_pricing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")
    for key, value in EXPLICIT_PRICES.items():
        monkeypatch.setenv(key, value)

    validate_prod_monetization_contract(strict=True)


def test_prod_accepts_catalog_defaults_when_explicit_overrides_are_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base(monkeypatch, tmp_path)
    monkeypatch.delenv("TELEGRAM_STARS_PRICING_MODE", raising=False)
    for key in EXPLICIT_PRICES:
        monkeypatch.delenv(key, raising=False)

    validate_prod_monetization_contract(strict=True)


def test_first_deploy_allows_only_uncommitted_env_migration_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker = _base(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")

    assert marker.exists() is False
    validate_prod_monetization_contract(strict=True)


def test_prod_rejects_telegram_yookassa_after_migration_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker = _base(monkeypatch, tmp_path)
    marker.write_text("applied\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")

    with pytest.raises(ValidationError, match="TELEGRAM_YOOKASSA_ENABLED must be 0"):
        validate_prod_monetization_contract(strict=True)


def test_prod_rejects_buyer_parity_stars_pricing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "buyer_parity")
    monkeypatch.setenv("TELEGRAM_STARS_BUYER_RUB_PER_XTR", "1.54905")

    with pytest.raises(ValidationError, match="must be explicit"):
        validate_prod_monetization_contract(strict=True)


def test_prod_rejects_invalid_stars_pricing_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "one_star_one_ruble")

    with pytest.raises(ValidationError, match="TELEGRAM_STARS_PRICING_MODE"):
        validate_prod_monetization_contract(strict=True)


def test_prod_rejects_drifted_explicit_package_price(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")
    monkeypatch.setenv("TELEGRAM_STARS_PRICE_PRACTICE_START_7", "1226")

    with pytest.raises(ValidationError, match="TELEGRAM_STARS_PRICE_PRACTICE_START_7 must be 1500"):
        validate_prod_monetization_contract(strict=True)
