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
LEGACY_INVOICE_MODULES = {
    "services/payments/subscription.py",
    "services/payments/gift.py",
}


def _read(path: str) -> str:
    try:
        return (PROJECT_ROOT / path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _legacy_invoice_routes_are_disabled() -> bool:
    text = _read("handlers/payments.py")
    return "Legacy" in text and "disabled" in text.lower() and "_sub_pick_disabled" in text and "_gift_buy_disabled" in text


def _py_files() -> list[Path]:
    return [
        p for p in PROJECT_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_DIRS for part in p.parts)
    ]


def validate_no_runtime_price_unit_heuristics(*, strict: bool = True) -> None:
    """Runtime must never guess whether prices are rubles or minor units.

    Legacy Telegram invoice modules are allowed to contain dead fallback code only
    after the public router disables their callback entrypoints. This keeps the
    release gate strict for reachable production paths without forcing a risky
    mass deletion of backward-compatibility helpers in the same deployment.
    """
    legacy_disabled = _legacy_invoice_routes_are_disabled()
    bad: list[str] = []
    for path in _py_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel == "services/migrations/price_rub_migration_v1.py":
            continue
        if legacy_disabled and rel in LEGACY_INVOICE_MODULES:
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
    text = _read("handlers/payments.py")
    if LEGACY_INVOICE_ROUTE.search(text) and not _legacy_invoice_routes_are_disabled():
        msg = "Legacy Telegram invoice routes are still reachable in handlers/payments.py"
        if strict:
            raise ValidationError(msg)


def validate_payment_contracts(*, strict: bool = True) -> None:
    validate_legacy_invoice_routes_disabled(strict=strict)
    validate_no_runtime_price_unit_heuristics(strict=strict)
    validate_delivery_contracts(strict=strict)
