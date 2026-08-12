from __future__ import annotations

"""Canonical commercial checkout telemetry.

Provider adapters call this only after they have actually created a payable
invoice/confirmation surface.  Tariff views are deliberately not checkout
starts.  The event is best-effort analytics/CRM evidence and must never become
an alternate payment or entitlement authority.
"""

from typing import Any

from services.events import log_runtime_event


def record_payment_started(
    user_id: int,
    *,
    provider: str,
    source: str,
    package_id: str,
    amount: int,
    currency: str,
    gift: bool = False,
    transport: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    uid = int(user_id)
    if uid <= 0:
        return

    payload: dict[str, Any] = {
        "provider": str(provider or "unknown").strip()[:40] or "unknown",
        "source": str(source or "unknown").strip()[:40] or "unknown",
        "package_id": str(package_id or "").strip()[:80],
        "amount": max(0, int(amount or 0)),
        "currency": str(currency or "").strip().upper()[:12],
        "gift": bool(gift),
        "transport": str(transport or "").strip()[:80],
    }
    for key, value in dict(extra or {}).items():
        safe_key = str(key or "").strip()[:80]
        if safe_key and safe_key not in payload:
            payload[safe_key] = value

    log_runtime_event(
        uid,
        event_type="payment_started",
        payload=payload,
        source=payload["source"],
    )
