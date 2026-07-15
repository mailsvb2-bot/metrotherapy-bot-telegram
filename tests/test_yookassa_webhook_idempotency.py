from __future__ import annotations

from services.db import db
from services.payments.reconciliation import record_yookassa_webhook


def _count(conn, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _wallet_available(conn, user_id: int) -> int:
    row = conn.execute("SELECT available_tokens FROM practice_wallets WHERE user_id=?", (int(user_id),)).fetchone()
    return int(row[0]) if row else 0


def _payment_state(*, payment_id: str) -> dict[str, str | None]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT processing_status, problem, processing_error, granted_at_utc, side_effects_done_at_utc
            FROM payments
            WHERE provider_charge_id=? OR telegram_charge_id=?
            LIMIT 1
            """.strip(),
            (payment_id, f"yookassa:{payment_id}"),
        ).fetchone()
    if not row:
        return {}
    return {
        "processing_status": row["processing_status"],
        "problem": row["problem"],
        "processing_error": row["processing_error"],
        "granted_at_utc": row["granted_at_utc"],
        "side_effects_done_at_utc": row["side_effects_done_at_utc"],
    }


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
        amount="24870.00",
    )

    before = _snapshot(user_id=user_id, payment_id=payment_id)
    first = record_yookassa_webhook(payload)
    after_first = _snapshot(user_id=user_id, payment_id=payment_id)
    second = record_yookassa_webhook(payload)
    after_second = _snapshot(user_id=user_id, payment_id=payment_id)
    state = _payment_state(payment_id=payment_id)

    assert first.ok is True
    assert first.inserted is True
    assert first.problem == ""
    assert first.processing_status == "side_effects_done"
    assert first.side_effects_done is True
    assert second.ok is True
    assert second.inserted is False
    assert second.problem == ""
    assert second.processing_status == "side_effects_done"
    assert second.side_effects_done is True

    assert state["processing_status"] == "side_effects_done"
    assert state["problem"] in ("", None)
    assert state["processing_error"] in ("", None)
    assert state["granted_at_utc"]
    assert state["side_effects_done_at_utc"]

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


def test_replay_recovers_payment_token_grant_marker_without_double_wallet_grant():
    user_id = 81002
    payment_id = "idem-personal-month-replay-marker"
    payload = _payload(
        payment_id=payment_id,
        user_id=user_id,
        package_id="practice_personal_month",
        amount="24870.00",
    )

    first = record_yookassa_webhook(payload)
    after_first = _snapshot(user_id=user_id, payment_id=payment_id)
    with db() as conn:
        conn.execute(
            "DELETE FROM payment_token_grants WHERE provider=? AND provider_payment_id=?",
            ("yookassa", payment_id),
        )
        conn.commit()
    after_marker_loss = _snapshot(user_id=user_id, payment_id=payment_id)
    replay = record_yookassa_webhook(payload)
    after_replay = _snapshot(user_id=user_id, payment_id=payment_id)

    assert first.ok is True
    assert first.processing_status == "side_effects_done"
    assert replay.ok is True
    assert replay.inserted is False
    assert replay.processing_status == "side_effects_done"
    assert replay.side_effects_done is True

    assert after_first["wallet_available"] == 60
    assert after_first["grant_ledger"] == 1
    assert after_marker_loss["wallet_available"] == 60
    assert after_marker_loss["payment_token_grants"] == 0
    assert after_replay["wallet_available"] == 60
    assert after_replay["grant_ledger"] == 1
    assert after_replay["payment_token_grants"] == 1
