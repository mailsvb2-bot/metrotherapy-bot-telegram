from __future__ import annotations

import json
from typing import Any

from core.time_utils import utc_now_iso
from services.db import db, tx
from services.payments.reconciliation import ReconciliationResult
from services.practice_tokens import get_wallet_in_conn, insert_ledger

_PROVIDER = "yookassa"
_EVENT = "refund.succeeded"


def _dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return dict(row)


def _minor(amount: Any) -> int:
    if not isinstance(amount, dict):
        return 0
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

    try:
        value = Decimal(str(amount.get("value") or "0").replace(",", ".")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        return 0
    return max(0, int(value * 100))


def _result(
    *,
    payment_id: str,
    inserted: bool,
    processing_status: str,
    problem: str = "",
    side_effects_done: bool = False,
) -> ReconciliationResult:
    # A verified provider fact is acknowledged even when local access revocation
    # needs operator review. Returning HTTP 400 would make YooKassa retry forever
    # without changing the deterministic conflict.
    return ReconciliationResult(
        ok=True,
        provider=_PROVIDER,
        provider_payment_id=payment_id,
        status="succeeded",
        event=_EVENT,
        inserted=bool(inserted),
        problem=problem,
        processing_status=processing_status,
        side_effects_done=side_effects_done,
    )


def _mark_action_required(
    conn: Any,
    *,
    refund_id: str,
    payment_id: str,
    cumulative_minor: int,
    payment_minor: int,
    problem: str,
    debt_tokens: int = 0,
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE yookassa_refunds
        SET status='action_required', problem=?, debt_tokens=?,
            cumulative_refunded_minor=?, payment_amount_minor=?,
            processed_at=COALESCE(processed_at, ?), updated_at=?
        WHERE refund_id=?
        """.strip(),
        (
            str(problem)[:500],
            max(0, int(debt_tokens)),
            max(0, int(cumulative_minor)),
            max(0, int(payment_minor)),
            now,
            now,
            refund_id,
        ),
    )
    conn.execute(
        """
        UPDATE payments
        SET processing_status='refund_action_required', problem=?, processing_error=?, reconciled_at=?
        WHERE provider_charge_id=? OR telegram_charge_id=?
        """.strip(),
        (str(problem)[:500], str(problem)[:500], now, payment_id, f"yookassa:{payment_id}"),
    )


def _mark_partial(
    conn: Any,
    *,
    refund_id: str,
    payment_id: str,
    cumulative_minor: int,
    payment_minor: int,
) -> str:
    problem = "partial_refund_requires_manual_policy"
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE yookassa_refunds
        SET status='partial_recorded', problem=?, cumulative_refunded_minor=?,
            payment_amount_minor=?, processed_at=COALESCE(processed_at, ?), updated_at=?
        WHERE refund_id=?
        """.strip(),
        (problem, int(cumulative_minor), int(payment_minor), now, now, refund_id),
    )
    conn.execute(
        """
        UPDATE payments
        SET processing_status='refund_partial_recorded', problem=?, processing_error='', reconciled_at=?
        WHERE provider_charge_id=? OR telegram_charge_id=?
        """.strip(),
        (problem, now, payment_id, f"yookassa:{payment_id}"),
    )
    return problem


def _premium_problem(conn: Any, payment_id: str) -> str:
    sent = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM premium_delivery_outbox
        WHERE idempotency_key LIKE ? AND status='sent'
        """.strip(),
        (f"premium_delivery:yookassa:{payment_id}:%",),
    ).fetchone()
    if sent and int(sent["n"] if hasattr(sent, "keys") else sent[0]) > 0:
        return "premium_content_already_delivered"

    active_consultation = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM consultation_requests
        WHERE provider='yookassa' AND provider_payment_id=?
          AND status NOT IN ('new','cancelled')
        """.strip(),
        (payment_id,),
    ).fetchone()
    if active_consultation and int(
        active_consultation["n"] if hasattr(active_consultation, "keys") else active_consultation[0]
    ) > 0:
        return "consultation_already_in_progress"
    return ""


def _payment_row(conn: Any, payment_id: str) -> dict[str, Any]:
    return _dict(
        conn.execute(
            """
            SELECT id, user_id, amount, currency, provider_status, processing_status, problem
            FROM payments
            WHERE provider_charge_id=? OR telegram_charge_id=?
            LIMIT 1
            """.strip(),
            (payment_id, f"yookassa:{payment_id}"),
        ).fetchone()
    )


def _gift_row(conn: Any, payment_id: str) -> dict[str, Any]:
    return _dict(
        conn.execute(
            """
            SELECT gift_token, buyer_user_id, recipient_user_id, package_id, status
            FROM gift_claims
            WHERE provider='yookassa' AND provider_payment_id=?
            LIMIT 1
            """.strip(),
            (payment_id,),
        ).fetchone()
    )


def _grant_and_lot(conn: Any, payment_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    grant = _dict(
        conn.execute(
            """
            SELECT user_id, package_id, tokens_granted
            FROM payment_token_grants
            WHERE provider='yookassa' AND provider_payment_id=?
            LIMIT 1
            """.strip(),
            (payment_id,),
        ).fetchone()
    )
    lot = _dict(
        conn.execute(
            """
            SELECT id, user_id, package_id, granted_tokens, available_tokens,
                   reserved_tokens, used_tokens, refund_held_tokens, refunded_tokens, refundable
            FROM practice_token_lots
            WHERE provider='yookassa' AND provider_payment_id=?
            LIMIT 1
            """.strip(),
            (payment_id,),
        ).fetchone()
    )
    return grant, lot


def _finalize_unclaimed_gift(
    conn: Any,
    *,
    refund_id: str,
    payment_id: str,
    gift: dict[str, Any],
    cumulative_minor: int,
    payment_minor: int,
) -> ReconciliationResult:
    status = str(gift.get("status") or "")
    gift_token = str(gift.get("gift_token") or "")
    if status in {"claiming", "claimed"}:
        debt = 0
        if gift_token:
            lot = _dict(
                conn.execute(
                    """
                    SELECT granted_tokens, available_tokens, reserved_tokens, used_tokens
                    FROM practice_token_lots
                    WHERE provider='gift_claim' AND provider_payment_id=?
                    LIMIT 1
                    """.strip(),
                    (gift_token,),
                ).fetchone()
            )
            debt = int(lot.get("used_tokens") or 0) + int(lot.get("reserved_tokens") or 0)
        problem = "gift_already_claimed"
        _mark_action_required(
            conn,
            refund_id=refund_id,
            payment_id=payment_id,
            cumulative_minor=cumulative_minor,
            payment_minor=payment_minor,
            problem=problem,
            debt_tokens=debt,
        )
        return _result(
            payment_id=payment_id,
            inserted=True,
            processing_status="refund_action_required",
            problem=problem,
        )

    now = utc_now_iso()
    conn.execute(
        "UPDATE gift_claims SET status='refunded' WHERE gift_token=? AND status IN ('created','paid')",
        (gift_token,),
    )
    conn.execute(
        """
        UPDATE payments
        SET provider_status='refunded', processing_status='refunded', problem='',
            processing_error='', reconciled_at=?
        WHERE provider_charge_id=? OR telegram_charge_id=?
        """.strip(),
        (now, payment_id, f"yookassa:{payment_id}"),
    )
    conn.execute(
        """
        UPDATE yookassa_refunds
        SET status='completed', problem='', cumulative_refunded_minor=?,
            payment_amount_minor=?, gift_token=?, processed_at=COALESCE(processed_at, ?), updated_at=?
        WHERE refund_id=?
        """.strip(),
        (int(cumulative_minor), int(payment_minor), gift_token, now, now, refund_id),
    )
    return _result(
        payment_id=payment_id,
        inserted=True,
        processing_status="refunded",
        side_effects_done=True,
    )


def _finalize_direct_access(
    conn: Any,
    *,
    refund_id: str,
    payment_id: str,
    payment: dict[str, Any],
    cumulative_minor: int,
    payment_minor: int,
) -> ReconciliationResult:
    grant, lot = _grant_and_lot(conn, payment_id)
    if not grant and not lot:
        # Charged payment with no entitlement: record the provider refund and close
        # the local payment without inventing wallet mutations.
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE payments
            SET provider_status='refunded', processing_status='refunded', problem='',
                processing_error='', reconciled_at=?
            WHERE provider_charge_id=? OR telegram_charge_id=?
            """.strip(),
            (now, payment_id, f"yookassa:{payment_id}"),
        )
        conn.execute(
            """
            UPDATE yookassa_refunds
            SET status='completed', problem='', cumulative_refunded_minor=?,
                payment_amount_minor=?, processed_at=COALESCE(processed_at, ?), updated_at=?
            WHERE refund_id=?
            """.strip(),
            (int(cumulative_minor), int(payment_minor), now, now, refund_id),
        )
        return _result(
            payment_id=payment_id,
            inserted=True,
            processing_status="refunded",
            side_effects_done=True,
        )

    if not grant or not lot:
        problem = "payment_token_provenance_incomplete"
        _mark_action_required(
            conn,
            refund_id=refund_id,
            payment_id=payment_id,
            cumulative_minor=cumulative_minor,
            payment_minor=payment_minor,
            problem=problem,
        )
        return _result(
            payment_id=payment_id,
            inserted=True,
            processing_status="refund_action_required",
            problem=problem,
        )

    tokens = int(lot.get("granted_tokens") or 0)
    available = int(lot.get("available_tokens") or 0)
    reserved = int(lot.get("reserved_tokens") or 0)
    used = int(lot.get("used_tokens") or 0)
    held = int(lot.get("refund_held_tokens") or 0)
    already_refunded = int(lot.get("refunded_tokens") or 0)
    refundable = int(lot.get("refundable") or 0)
    provenance_matches = (
        int(grant.get("user_id") or 0) == int(lot.get("user_id") or 0)
        and str(grant.get("package_id") or "") == str(lot.get("package_id") or "")
        and int(grant.get("tokens_granted") or 0) == tokens
    )
    if not provenance_matches:
        problem = "payment_token_provenance_conflict"
        _mark_action_required(
            conn,
            refund_id=refund_id,
            payment_id=payment_id,
            cumulative_minor=cumulative_minor,
            payment_minor=payment_minor,
            problem=problem,
        )
        return _result(
            payment_id=payment_id,
            inserted=True,
            processing_status="refund_action_required",
            problem=problem,
        )

    if already_refunded == tokens and tokens > 0:
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE yookassa_refunds
            SET status='completed', problem='', tokens_affected=?, cumulative_refunded_minor=?,
                payment_amount_minor=?, processed_at=COALESCE(processed_at, ?), updated_at=?
            WHERE refund_id=?
            """.strip(),
            (tokens, int(cumulative_minor), int(payment_minor), now, now, refund_id),
        )
        return _result(
            payment_id=payment_id,
            inserted=False,
            processing_status="refunded",
            side_effects_done=True,
        )

    if tokens <= 0 or refundable != 1 or available != tokens or reserved or used or held or already_refunded:
        problem = "purchased_practices_already_used_or_reserved"
        _mark_action_required(
            conn,
            refund_id=refund_id,
            payment_id=payment_id,
            cumulative_minor=cumulative_minor,
            payment_minor=payment_minor,
            problem=problem,
            debt_tokens=max(0, reserved + used + held),
        )
        return _result(
            payment_id=payment_id,
            inserted=True,
            processing_status="refund_action_required",
            problem=problem,
        )

    premium_problem = _premium_problem(conn, payment_id)
    if premium_problem:
        _mark_action_required(
            conn,
            refund_id=refund_id,
            payment_id=payment_id,
            cumulative_minor=cumulative_minor,
            payment_minor=payment_minor,
            problem=premium_problem,
        )
        return _result(
            payment_id=payment_id,
            inserted=True,
            processing_status="refund_action_required",
            problem=premium_problem,
        )

    user_id = int(lot.get("user_id") or payment.get("user_id") or 0)
    wallet = get_wallet_in_conn(conn, user_id)
    if int(wallet.available_tokens) < tokens:
        problem = "refund_wallet_balance_conflict"
        _mark_action_required(
            conn,
            refund_id=refund_id,
            payment_id=payment_id,
            cumulative_minor=cumulative_minor,
            payment_minor=payment_minor,
            problem=problem,
            debt_tokens=tokens - int(wallet.available_tokens),
        )
        return _result(
            payment_id=payment_id,
            inserted=True,
            processing_status="refund_action_required",
            problem=problem,
        )

    now = utc_now_iso()
    cursor = conn.execute(
        """
        UPDATE practice_wallets
        SET available_tokens=available_tokens-?, refunded_tokens=refunded_tokens+?,
            updated_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND available_tokens>=?
        """.strip(),
        (tokens, tokens, user_id, tokens),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) != 1:
        raise RuntimeError("yookassa_refund_wallet_race")

    cursor = conn.execute(
        """
        UPDATE practice_token_lots
        SET available_tokens=0, refunded_tokens=refunded_tokens+?, refundable=0,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND available_tokens=? AND reserved_tokens=0 AND used_tokens=0
          AND refund_held_tokens=0 AND refunded_tokens=0
        """.strip(),
        (tokens, int(lot["id"]), tokens),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) != 1:
        raise RuntimeError("yookassa_refund_lot_race")

    wallet_after = get_wallet_in_conn(conn, user_id)
    insert_ledger(
        conn,
        user_id=user_id,
        event_type="refund",
        amount=-tokens,
        balance_after=wallet_after.available_tokens,
        reason="yookassa_refund_succeeded",
        source="yookassa_refund_webhook",
        package_id=str(lot.get("package_id") or ""),
        provider=_PROVIDER,
        provider_payment_id=payment_id,
        idempotency_key=f"yookassa_refund_finalize:{payment_id}",
    )
    conn.execute(
        """
        UPDATE premium_entitlements
        SET status='revoked', updated_at=CURRENT_TIMESTAMP
        WHERE provider='yookassa' AND provider_payment_id=? AND status IN ('active','refund_pending')
        """.strip(),
        (payment_id,),
    )
    conn.execute(
        """
        UPDATE premium_delivery_outbox
        SET status='cancelled', updated_at=CURRENT_TIMESTAMP
        WHERE idempotency_key LIKE ? AND status IN ('pending','retry','failed','dead','refund_pending')
        """.strip(),
        (f"premium_delivery:yookassa:{payment_id}:%",),
    )
    conn.execute(
        """
        UPDATE consultation_requests
        SET status='cancelled', updated_at=CURRENT_TIMESTAMP
        WHERE provider='yookassa' AND provider_payment_id=? AND status='new'
        """.strip(),
        (payment_id,),
    )
    conn.execute(
        """
        UPDATE payments
        SET provider_status='refunded', processing_status='refunded', problem='',
            processing_error='', reconciled_at=?
        WHERE provider_charge_id=? OR telegram_charge_id=?
        """.strip(),
        (now, payment_id, f"yookassa:{payment_id}"),
    )
    conn.execute(
        """
        UPDATE yookassa_refunds
        SET user_id=?, package_id=?, tokens_affected=?, debt_tokens=0,
            status='completed', problem='', cumulative_refunded_minor=?,
            payment_amount_minor=?, processed_at=COALESCE(processed_at, ?), updated_at=?
        WHERE refund_id=?
        """.strip(),
        (
            user_id,
            str(lot.get("package_id") or ""),
            tokens,
            int(cumulative_minor),
            int(payment_minor),
            now,
            now,
            refund_id,
        ),
    )
    return _result(
        payment_id=payment_id,
        inserted=True,
        processing_status="refunded",
        side_effects_done=True,
    )


def record_yookassa_refund(payload: dict[str, Any]) -> ReconciliationResult:
    event = str(payload.get("event") or "").strip().lower()
    raw_object = payload.get("object")
    obj: dict[str, Any] = raw_object if isinstance(raw_object, dict) else {}
    refund_id = str(obj.get("id") or "").strip()
    payment_id = str(obj.get("payment_id") or "").strip()
    status = str(obj.get("status") or "").strip().lower()
    raw_amount = obj.get("amount")
    amount: dict[str, Any] = raw_amount if isinstance(raw_amount, dict) else {}
    amount_minor = _minor(amount)
    currency = str(amount.get("currency") or "RUB").strip().upper()

    if event != _EVENT or status != "succeeded" or not refund_id or not payment_id:
        return ReconciliationResult(
            ok=False,
            provider=_PROVIDER,
            provider_payment_id=payment_id,
            status=status or "unknown",
            event=event,
            inserted=False,
            problem="invalid_refund_provider_fact",
            processing_status="action_required",
            side_effects_done=False,
        )
    if amount_minor <= 0 or not currency:
        return ReconciliationResult(
            ok=False,
            provider=_PROVIDER,
            provider_payment_id=payment_id,
            status=status,
            event=event,
            inserted=False,
            problem="invalid_refund_amount",
            processing_status="action_required",
            side_effects_done=False,
        )

    provider_raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:12000]
    with db() as conn:
        with tx(conn):
            payment = _payment_row(conn, payment_id)
            if not payment:
                inserted_cursor = conn.execute(
                    """
                    INSERT INTO yookassa_refunds(
                        refund_id, payment_id, amount_minor, currency, status, problem, provider_raw
                    ) VALUES(?,?,?,?, 'action_required', 'payment_not_found', ?)
                    ON CONFLICT(refund_id) DO NOTHING
                    """.strip(),
                    (refund_id, payment_id, amount_minor, currency, provider_raw),
                )
                return _result(
                    payment_id=payment_id,
                    inserted=int(getattr(inserted_cursor, "rowcount", 0) or 0) == 1,
                    processing_status="refund_action_required",
                    problem="payment_not_found",
                )

            payment_minor = int(payment.get("amount") or 0)
            payment_currency = str(payment.get("currency") or "RUB").strip().upper()
            if payment_minor <= 0 or payment_currency != currency:
                return _result(
                    payment_id=payment_id,
                    inserted=False,
                    processing_status="refund_action_required",
                    problem="refund_payment_amount_or_currency_conflict",
                )

            inserted_cursor = conn.execute(
                """
                INSERT INTO yookassa_refunds(
                    refund_id, payment_id, user_id, amount_minor, currency,
                    payment_amount_minor, status, provider_raw
                ) VALUES(?,?,?,?,?,?, 'received', ?)
                ON CONFLICT(refund_id) DO NOTHING
                """.strip(),
                (
                    refund_id,
                    payment_id,
                    int(payment.get("user_id") or 0),
                    amount_minor,
                    currency,
                    payment_minor,
                    provider_raw,
                ),
            )
            inserted = int(getattr(inserted_cursor, "rowcount", 0) or 0) == 1
            existing = _dict(
                conn.execute(
                    """
                    SELECT payment_id, amount_minor, currency, status, problem
                    FROM yookassa_refunds WHERE refund_id=?
                    """.strip(),
                    (refund_id,),
                ).fetchone()
            )
            if (
                str(existing.get("payment_id") or "") != payment_id
                or int(existing.get("amount_minor") or 0) != amount_minor
                or str(existing.get("currency") or "").upper() != currency
            ):
                return _result(
                    payment_id=payment_id,
                    inserted=False,
                    processing_status="refund_action_required",
                    problem="refund_idempotency_conflict",
                )

            if str(payment.get("provider_status") or "") == "refunded":
                now = utc_now_iso()
                conn.execute(
                    """
                    UPDATE yookassa_refunds
                    SET status='completed', problem='', cumulative_refunded_minor=?,
                        payment_amount_minor=?, processed_at=COALESCE(processed_at, ?), updated_at=?
                    WHERE refund_id=?
                    """.strip(),
                    (payment_minor, payment_minor, now, now, refund_id),
                )
                return _result(
                    payment_id=payment_id,
                    inserted=inserted,
                    processing_status="refunded",
                    side_effects_done=True,
                )

            total_row = conn.execute(
                "SELECT COALESCE(SUM(amount_minor),0) AS total FROM yookassa_refunds WHERE payment_id=?",
                (payment_id,),
            ).fetchone()
            cumulative_minor = int(
                total_row["total"] if hasattr(total_row, "keys") else total_row[0]
            )
            if cumulative_minor < payment_minor:
                problem = _mark_partial(
                    conn,
                    refund_id=refund_id,
                    payment_id=payment_id,
                    cumulative_minor=cumulative_minor,
                    payment_minor=payment_minor,
                )
                return _result(
                    payment_id=payment_id,
                    inserted=inserted,
                    processing_status="refund_partial_recorded",
                    problem=problem,
                )
            if cumulative_minor > payment_minor:
                problem = "cumulative_refund_exceeds_payment"
                _mark_action_required(
                    conn,
                    refund_id=refund_id,
                    payment_id=payment_id,
                    cumulative_minor=cumulative_minor,
                    payment_minor=payment_minor,
                    problem=problem,
                )
                return _result(
                    payment_id=payment_id,
                    inserted=inserted,
                    processing_status="refund_action_required",
                    problem=problem,
                )

            gift = _gift_row(conn, payment_id)
            if gift:
                result = _finalize_unclaimed_gift(
                    conn,
                    refund_id=refund_id,
                    payment_id=payment_id,
                    gift=gift,
                    cumulative_minor=cumulative_minor,
                    payment_minor=payment_minor,
                )
            else:
                result = _finalize_direct_access(
                    conn,
                    refund_id=refund_id,
                    payment_id=payment_id,
                    payment=payment,
                    cumulative_minor=cumulative_minor,
                    payment_minor=payment_minor,
                )
            return ReconciliationResult(
                ok=result.ok,
                provider=result.provider,
                provider_payment_id=result.provider_payment_id,
                status=result.status,
                event=result.event,
                inserted=inserted,
                problem=result.problem,
                processing_status=result.processing_status,
                side_effects_done=result.side_effects_done,
            )
