from __future__ import annotations

import pytest

from services.practice_token_contract import (
    daily_practice_cost,
    normalize_delivery_mode,
    package_by_id,
    public_practice_packages,
    telegram_stars_price,
    telegram_yookassa_enabled,
)


PUBLIC_PRICE_LADDER = {
    "practice_start_7": (2499, 1500),
    "practice_60": (4199, 2500),
    "practice_antistress_60": (8290, 5000),
    "practice_personal_month": (24870, 15000),
}


def test_public_practice_packages_are_current_ladder():
    package_ids = [package.package_id for package in public_practice_packages()]

    assert package_ids == list(PUBLIC_PRICE_LADDER)


def test_practice_package_rub_and_stars_prices_are_locked(monkeypatch):
    monkeypatch.delenv("TELEGRAM_STARS_PRICING_MODE", raising=False)
    for package_id in PUBLIC_PRICE_LADDER:
        env_key = "TELEGRAM_STARS_PRICE_" + "".join(
            ch if ch.isalnum() else "_" for ch in package_id.upper()
        )
        monkeypatch.delenv(env_key, raising=False)

    for package_id, (price_rub, price_xtr) in PUBLIC_PRICE_LADDER.items():
        package = package_by_id(package_id)
        assert package.price_rub == price_rub
        assert package.price_xtr == price_xtr
        assert telegram_stars_price(package_id) == price_xtr


def test_explicit_stars_env_can_override_catalog_for_emergency(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")
    monkeypatch.setenv("TELEGRAM_STARS_PRICE_PRACTICE_START_7", "1700")

    assert telegram_stars_price("practice_start_7") == 1700


def test_buyer_parity_remains_an_explicit_legacy_mode(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "buyer_parity")
    monkeypatch.setenv("TELEGRAM_STARS_BUYER_RUB_PER_XTR", "1.54905")

    assert telegram_stars_price("practice_start_7") == 1613
    assert telegram_stars_price("practice_60") == 2710
    assert telegram_stars_price("practice_antistress_60") == 5351
    assert telegram_stars_price("practice_personal_month") == 16055


def test_telegram_yookassa_has_independent_kill_switch(monkeypatch):
    monkeypatch.delenv("TELEGRAM_YOOKASSA_ENABLED", raising=False)
    assert telegram_yookassa_enabled() is True

    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")
    assert telegram_yookassa_enabled() is False

    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    assert telegram_yookassa_enabled() is True


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
