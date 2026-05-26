from __future__ import annotations

import pytest

from services.practice_token_contract import (
    daily_practice_cost,
    normalize_delivery_mode,
    package_by_id,
    public_practice_packages,
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


def test_legacy_packages_are_not_public_but_still_resolvable():
    public_ids = {package.package_id for package in public_practice_packages()}

    assert "practice_5" not in public_ids
    assert package_by_id("practice_5").tokens == 5


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
