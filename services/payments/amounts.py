from __future__ import annotations

from typing import Any


MAX_TELEGRAM_MINOR_AMOUNT = 2_147_483_647


class PaymentAmountError(ValueError):
    """Raised when a plan price cannot be converted into a safe invoice amount."""


def price_rub_from_plan(plan: dict[str, Any] | None) -> int:
    """Return the canonical plan price in rubles.

    Contract: ``plans.price`` is rubles, never kopecks. Legacy kopeck rows are
    normalized by the one-time ``price_rub_migration_v1`` migration. Runtime code
    must not guess units with heuristics such as ``>= 50000 and % 100 == 0``:
    that would silently corrupt legitimate high-ticket prices.
    """
    if not plan:
        raise PaymentAmountError("plan is empty")
    try:
        price_rub = int(plan.get("price") or 0)
    except (TypeError, ValueError) as exc:
        raise PaymentAmountError("plan price is not an integer ruble amount") from exc
    if price_rub <= 0:
        raise PaymentAmountError("plan price must be positive")
    return price_rub


def amount_minor_from_rub(price_rub: int) -> int:
    """Convert rubles to Telegram/YooKassa minor units (kopecks)."""
    try:
        amount = int(price_rub) * 100
    except (TypeError, ValueError) as exc:
        raise PaymentAmountError("price_rub is not an integer") from exc
    if amount <= 0:
        raise PaymentAmountError("minor amount must be positive")
    if amount > MAX_TELEGRAM_MINOR_AMOUNT:
        raise PaymentAmountError("minor amount exceeds Telegram provider limit")
    return amount


def amount_minor_from_plan(plan: dict[str, Any] | None) -> int:
    return amount_minor_from_rub(price_rub_from_plan(plan))
