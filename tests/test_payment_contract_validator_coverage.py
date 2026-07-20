from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.validators import payment_contracts


def test_payment_contract_file_helpers_and_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    handlers = tmp_path / "handlers"
    payments = tmp_path / "services" / "payments"
    migrations = tmp_path / "services" / "migrations"
    ignored = tmp_path / ".venv"
    for directory in (handlers, payments, migrations, ignored):
        directory.mkdir(parents=True)
    monkeypatch.setattr(payment_contracts, "PROJECT_ROOT", tmp_path)

    assert payment_contracts._read("missing.py") == ""
    handler = handlers / "payments.py"
    handler.write_text(
        "Legacy disabled _sub_pick_disabled _gift_buy_disabled sub:buy:",
        encoding="utf-8",
    )
    assert payment_contracts._legacy_invoice_routes_are_disabled() is True

    good = payments / "good.py"
    good.write_text("safe price handling", encoding="utf-8")
    ignored_file = ignored / "ignored.py"
    ignored_file.write_text("forbidden marker", encoding="utf-8")
    assert good in payment_contracts._py_files()
    assert ignored_file not in payment_contracts._py_files()

    monkeypatch.setattr(
        payment_contracts,
        "PRICE_UNIT_HEURISTIC",
        re.compile(r"FORBIDDEN_PRICE_MARKER"),
    )
    payment_contracts.validate_no_runtime_price_unit_heuristics(strict=True)
    bad = payments / "bad_price.py"
    bad.write_text("FORBIDDEN_PRICE_MARKER", encoding="utf-8")
    payment_contracts.validate_no_runtime_price_unit_heuristics(strict=False)
    with pytest.raises(payment_contracts.ValidationError, match="price-unit heuristic"):
        payment_contracts.validate_no_runtime_price_unit_heuristics(strict=True)

    migration = migrations / "price_rub_migration_v1.py"
    migration.write_text("FORBIDDEN_PRICE_MARKER", encoding="utf-8")
    bad.unlink()
    payment_contracts.validate_no_runtime_price_unit_heuristics(strict=True)

    payment_contracts.validate_legacy_invoice_routes_disabled(strict=True)
    handler.write_text("sub:buy: active", encoding="utf-8")
    payment_contracts.validate_legacy_invoice_routes_disabled(strict=False)
    with pytest.raises(payment_contracts.ValidationError, match="still reachable"):
        payment_contracts.validate_legacy_invoice_routes_disabled(strict=True)
    handler.write_text("no legacy route", encoding="utf-8")

    payment_contracts.validate_no_legacy_provider_token_in_payment_runtime(strict=True)
    legacy = payments / "legacy.py"
    legacy.write_text("PAY_PROVIDER_TOKEN", encoding="utf-8")
    payment_contracts.validate_no_legacy_provider_token_in_payment_runtime(strict=False)
    with pytest.raises(payment_contracts.ValidationError, match="forbidden"):
        payment_contracts.validate_no_legacy_provider_token_in_payment_runtime(strict=True)


def test_validate_payment_contracts_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        payment_contracts, "validate_legacy_invoice_routes_disabled",
        lambda *, strict: calls.append(("legacy", strict)),
    )
    monkeypatch.setattr(
        payment_contracts, "validate_no_runtime_price_unit_heuristics",
        lambda *, strict: calls.append(("price", strict)),
    )
    monkeypatch.setattr(
        payment_contracts, "validate_no_legacy_provider_token_in_payment_runtime",
        lambda *, strict: calls.append(("provider", strict)),
    )
    monkeypatch.setattr(
        payment_contracts, "validate_delivery_contracts",
        lambda *, strict: calls.append(("delivery", strict)),
    )
    payment_contracts.validate_payment_contracts(strict=False)
    assert calls == [
        ("legacy", False), ("price", False),
        ("provider", False), ("delivery", False),
    ]
