from __future__ import annotations

from pathlib import Path

from services.practice_token_contract import package_by_id


def test_premium_package_operator_docs_match_canonical_rub_prices():
    doc = Path("docs/PREMIUM_PACKAGE_ENV_CONTRACT.md").read_text(encoding="utf-8")

    for package_id in ("practice_antistress_60", "practice_personal_month"):
        package = package_by_id(package_id)
        assert f"`{package_id}`: `{package.price_rub}.00 RUB`." in doc


def test_premium_package_operator_docs_name_checkout_contract_as_source_of_truth():
    doc = Path("docs/PREMIUM_PACKAGE_ENV_CONTRACT.md").read_text(encoding="utf-8")

    assert "services.practice_token_contract.DEFAULT_PRACTICE_PACKAGES" in doc
    assert "telegram_stars_price()" in doc
