from __future__ import annotations

from services.db import db
from services.payments.reconciliation import record_yookassa_webhook


def _count(conn, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _wallet_available(conn, user_id: int) -> int:
    row = conn.execute("SELECT available_tokens FROM practice_wallets WHERE user_id=?", (int(user_id),)).fetchone()
    return int(row[0]) if row else 0


def _snapshot(*, user_id: int, payment_id: str) -> dict[str, int]:
    with db() as conn:
        return {
            "wallet_available": _wallet_available(conn, user_id),
            "payments": _count(
                conn,
                "SELECT COUNT(*) FROM payments WHERE provider_charge_id=? OR telegram_charge_id=?",
                (payment_id, f"yookassa:{payment_id}"),
            ),
            "payment_token_grants": _count(
                conn,
                "SELECT COUNT(*) FROM payment_token_grants WHERE provider=? AND provider_payment_id=?",
                ("yookassa", payment_id),
            ),
            "grant_ledger": _count(
                conn,
                "SELECT COUNT(*) FROM practice_ledger WHERE provider=? AND provider_payment_id=? AND event_type='grant'",
                ("yookassa", payment_id),
            ),
            "premium_entitlements": _count(
                conn,
                "SELECT COUNT(*) FROM premium_entitlements WHERE provider=? AND provider_payment_id=?",
                ("yookassa", payment_id),
            ),
            "premium_outbox": _count(
                conn,
                "SELECT COUNT(*) FROM premium_delivery_outbox WHERE idempotency_key LIKE ?",
                (f"%{payment_id}%",),
            ),
            "consultation_requests": _count(
                conn,
                "SELECT COUNT(*) FROM consultation_requests WHERE provider=? AND provider_payment_id=?",
                ("yookassa", payment_id),
            ),
        }


def _diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in sorted(set(before) | set(after))}


def _payload(*, payment_id: str, user_id: int, package_id: str, amount: str) -> dict:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": amount, "currency": "RUB"},
            "metadata": {
                "project": "metrotherapy",
                "user_id": str(int(user_id)),
                "external_user_id": str(int(user_id)),
                "source": "telegram",
                "kind": "tokens",
                "package_id": package_id,
            },
        },
    }


def test_duplicate_personal_month_webhook_does_not_double_grant():
    user_id = 81001
    payment_id = "idem-personal-month-1"
    payload = _payload(
        payment_id=payment_id,
        user_id=user_id,
        package_id="practice_personal_month",
        amount="23000.00",
    )

    before = _snapshot(user_id=user_id, payment_id=payment_id)
    first = record_yookassa_webhook(payload)
    after_first = _snapshot(user_id=user_id, payment_id=payment_id)
    second = record_yookassa_webhook(payload)
    after_second = _snapshot(user_id=user_id, payment_id=payment_id)

    assert first.ok is True
    assert first.inserted is True
    assert first.problem == ""
    assert second.ok is True
    assert second.inserted is False
    assert second.problem == ""

    first_delta = _diff(before, after_first)
    second_delta = _diff(after_first, after_second)

    assert first_delta["wallet_available"] == 60
    assert first_delta["payments"] == 1
    assert first_delta["payment_token_grants"] == 1
    assert first_delta["grant_ledger"] == 1
    assert first_delta["premium_entitlements"] == 2
    assert first_delta["premium_outbox"] == 2
    assert first_delta["consultation_requests"] == 1

    for key in (
        "wallet_available",
        "payments",
        "payment_token_grants",
        "grant_ledger",
        "premium_entitlements",
        "premium_outbox",
        "consultation_requests",
    ):
        assert second_delta[key] == 0
