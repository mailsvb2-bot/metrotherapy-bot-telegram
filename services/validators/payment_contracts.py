from __future__ import annotations

import re
from pathlib import Path

from core.paths import ROOT as PROJECT_ROOT
from services.validators.base import ValidationError
from services.validators.delivery_contracts import validate_delivery_contracts

EXCLUDED_DIRS = {".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache"}

PRICE_UNIT_HEURISTIC = re.compile(
    r"price_rub\s*>=\s*(?:50000|100000).*?price_rub\s*=\s*price_rub\s*//\s*100",
    re.DOTALL,
)

LEGACY_INVOICE_ROUTE = re.compile(r"sub:buy:|pay:selected|gift:buy:")


def _py_files() -> list[Path]:
    return [
        p for p in PROJECT_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_DIRS for part in p.parts)
    ]


def validate_no_runtime_price_unit_heuristics(*, strict: bool = True) -> None:
    """Runtime must never guess whether prices are rubles or minor units."""
    bad: list[str] = []
    for path in _py_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel == "services/migrations/price_rub_migration_v1.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if PRICE_UNIT_HEURISTIC.search(text):
            bad.append(rel)
    if bad:
        msg = "Forbidden runtime price-unit heuristic found: " + ", ".join(sorted(set(bad)))
        if strict:
            raise ValidationError(msg)


def validate_legacy_invoice_routes_disabled(*, strict: bool = True) -> None:
    """Legacy Telegram invoice routes must not be public while token checkout is canonical."""
    path = PROJECT_ROOT / "handlers" / "payments.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    if LEGACY_INVOICE_ROUTE.search(text) and "Legacy" not in text and "disabled" not in text.lower():
        msg = "Legacy Telegram invoice routes are still reachable in handlers/payments.py"
        if strict:
            raise ValidationError(msg)


def validate_payment_contracts(*, strict: bool = True) -> None:
    validate_no_runtime_price_unit_heuristics(strict=strict)
    validate_legacy_invoice_routes_disabled(strict=strict)
    validate_delivery_contracts(strict=strict)
