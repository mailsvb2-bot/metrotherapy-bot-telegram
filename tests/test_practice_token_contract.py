from __future__ import annotations

import pytest

from services.practice_token_contract import (
    daily_practice_cost,
    normalize_delivery_mode,
    package_by_id,
    public_practice_packages,
    telegram_stars_buyer_rub_per_xtr,
    telegram_stars_price,
    telegram_yookassa_enabled,
)


def test_public_practice_packages_are_current_ladder():
    package_ids = [package.package_id for package in public_practice_packages()]

    assert package_ids == [
        "practice_start_7",
        "practice_60",
        "practice_antistress_60",
        "practice_personal_month",
    ]


def test_practice_package_prices_are_locked():
    assert package_by_id("practice_start_7").price_rub == 1900
    assert package_by_id("practice_60").price_rub == 7900
    assert package_by_id("practice_antistress_60").price_rub == 12900
    assert package_by_id("practice_personal_month").price_rub == 23000


def test_default_stars_prices_use_buyer_parity_reference(monkeypatch):
    monkeypatch.delenv("TELEGRAM_STARS_PRICING_MODE", raising=False)
    monkeypatch.delenv("TELEGRAM_STARS_BUYER_RUB_PER_XTR", raising=False)

    assert telegram_stars_price("practice_start_7") == 1226
    assert telegram_stars_price("practice_60") == 5099
    assert telegram_stars_price("practice_antistress_60") == 8327
    assert telegram_stars_price("practice_personal_month") == 14847

    reference = telegram_stars_buyer_rub_per_xtr()
    for package in public_practice_packages():
        estimated_cost = telegram_stars_price(package.package_id) * reference
        assert estimated_cost <= package.price_rub
        assert package.price_rub - estimated_cost < reference


def test_explicit_stars_price_requires_explicit_mode(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")
    monkeypatch.setenv("TELEGRAM_STARS_PRICE_PRACTICE_START_7", "1700")

    assert telegram_stars_price("practice_start_7") == 1700


def test_telegram_yookassa_is_permanently_disabled_for_digital_packages(monkeypatch):
    monkeypatch.delenv("TELEGRAM_YOOKASSA_ENABLED", raising=False)
    assert telegram_yookassa_enabled() is False

    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    assert telegram_yookassa_enabled() is False


def test_invalid_buyer_parity_reference_is_rejected(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "buyer_parity")
    monkeypatch.setenv("TELEGRAM_STARS_BUYER_RUB_PER_XTR", "0")

    with pytest.raises(ValueError):
        telegram_stars_price("practice_start_7")


def test_practice_package_titles_are_localized_public_ladder():
    titles = [package.title for package in public_practice_packages()]

    assert titles == [
        "\u0421\u0442\u0430\u0440\u0442\u043e\u0432\u044b\u0439 \u043f\u0430\u043a\u0435\u0442",
        "\u041f\u043e\u043b\u043d\u044b\u0439 \u043c\u0430\u0440\u0448\u0440\u0443\u0442",
        "\u0410\u043d\u0442\u0438\u0441\u0442\u0440\u0435\u0441\u0441-\u0441\u0438\u0441\u0442\u0435\u043c\u0430",
        "\u041f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u043c\u0435\u0441\u044f\u0446",
    ]


def test_legacy_packages_are_not_public_but_still_resolvable():
    public_ids = {package.package_id for package in public_practice_packages()}

    assert "practice_5" not in public_ids
    assert "practice_20" not in public_ids
    assert package_by_id("practice_5").tokens == 5
    assert package_by_id("practice_20").tokens == 20


def test_delivery_mode_costs():
    assert normalize_delivery_mode(None) == "single_daily"
    assert daily_practice_cost("single_daily") == 1
    assert daily_practice_cost("both") == 2
    assert daily_practice_cost("paused") == 0


def test_localized_delivery_mode_aliases():
    assert normalize_delivery_mode("\u0443\u0442\u0440\u043e") == "morning_only"
    assert normalize_delivery_mode("\u0432\u0435\u0447\u0435\u0440") == "evening_only"
    assert normalize_delivery_mode("\u0443\u0442\u0440\u043e + \u0432\u0435\u0447\u0435\u0440") == "both"
    assert normalize_delivery_mode("\u043f\u0430\u0443\u0437\u0430") == "paused"


def test_unknown_package_rejected():
    with pytest.raises(ValueError):
        package_by_id("missing")
