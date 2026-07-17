from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "services" / "privacy_manifest.py"

CANONICAL = '''_RETAINED: tuple[tuple[str, tuple[str, ...], str, bool], ...] = (
    ("subscriptions", ("user_id",), "legacy purchased-access fact", True),
    ("payments", ("user_id",), "payment, refund, dispute and accounting fact", True),
    ("payment_events", ("user_id",), "provider payment idempotency fact", True),
    (
        "payment_reconciliation_retry",
        ("user_id",),
        "provider-verified payment fulfilment retry and audit fact",
        True,
    ),
    (
        "gift_codes",
        ("created_by", "recipient_id", "redeemed_by", "claimed_by"),
        "gift accounting and ownership fact",
        False,
    ),
    ("gift_claims", ("buyer_user_id", "recipient_user_id"), "paid gift ownership and refund fact", True),
    ("practice_wallets", ("user_id",), "current purchased balance", True),
    ("practice_ledger", ("user_id",), "immutable token accounting ledger", True),
    ("payment_token_grants", ("user_id",), "payment-to-entitlement provenance", True),
    ("practice_reservations", ("user_id",), "purchased-token reservation accounting", True),
    ("user_practice_preferences", ("user_id",), "fulfilment setting for purchased access", True),
    ("practice_token_lots", ("user_id",), "exact payment-lot provenance and refunds", True),
    ("premium_entitlements", ("user_id",), "purchased premium entitlement", True),
    ("premium_delivery_outbox", ("user_id",), "premium fulfilment evidence", True),
    ("consultation_requests", ("user_id",), "paid consultation fulfilment", True),
    (
        "telegram_stars_refunds",
        ("payment_user_id", "beneficiary_user_id", "requested_by"),
        "provider refund state and audit",
        True,
    ),
    ("yookassa_refunds", ("user_id",), "provider refund state and audit", True),
    ("sales_lead_revenue", ("user_id",), "currency-specific revenue accounting fact", True),
    ("privacy_erasure_log", ("user_id",), "compliance evidence that erasure occurred", True),
)

'''


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    start = text.index("_RETAINED: tuple[")
    end = text.index("_POLICIES = (", start)
    normalized = text[:start] + CANONICAL + text[end:]
    TARGET.write_text(normalized, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
