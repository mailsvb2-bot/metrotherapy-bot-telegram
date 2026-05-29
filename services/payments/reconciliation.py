from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from services.db import db, tx
from services.gift_claims import is_gift_token, mark_gift_paid, normalize_gift_token
from services.messenger.platforms import normalize_platform
from services.practice_token_contract import package_by_id
from services.practice_tokens import grant_tokens_for_payment
from services.premium_entitlements import grant_premium_entitlements_for_payment

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _amount_to_minor_units(amount: dict[str, Any] | None) -> int:
    if not amount:
        return 0
    raw = str(amount.get("value") or "0").replace(",", ".").strip()
    try:
        value = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return 0
    return int(value * 100)


def _metadata_user_id(metadata: dict[str, Any] | None) -> int:
    if not metadata:
        return 0
    for key in ("external_user_id", "user_id", "telegram_user_id"):
        value = str(metadata.get(key) or "").strip()
        if value.isdigit():
            return int(value)
    return 0


def _metadata_platform(metadata: dict[str, Any] | None) -> str:
    if not metadata:
        return "telegram"
    return normalize_platform(str(metadata.get("source") or metadata.get("platform") or "telegram"))


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    provider: str
    provider_payment_id: str
    status: str
    event: str
    inserted: bool
    problem: str = ""


def _practice_package_payment_problem(*, package_id: str, amount_minor: int, currency: str) -> str:
    try:
        package = package_by_id(package_id)
    except ValueError:
        return "unknown_package_id_for_practice_grant"
    if str(currency or "").strip().upper() != "RUB":
        return "currency_mismatch_for_practice_grant"
    expected_minor = int(package.price_rub) * 100
    if int(amount_minor) != expected_minor:
        return "amount_mismatch_for_practice_grant"
    return ""


def _mark_paid_gift_if_needed(
    *,
    event: str,
    status: str,
    payment_id: str,
    user_id: int,
    metadata: dict[str, Any],
    package_id: str,
) -> str:
    succeeded = event == "payment.succeeded" or status == "succeeded"
    if not succeeded:
        return ""
    gift_token = normalize_gift_token(str(metadata.get("gift_token") or ""))
    if not gift_token:
        return ""
    if not is_gift_token(gift_token):
        return "invalid_gift_token"
    if not package_id:
        return "missing_package_id_for_gift_claim"
    try:
        mark_gift_paid(
            gift_token=gift_token,
            buyer_user_id=int(user_id or 0),
            package_id=package_id,
            provider="yookassa",
            provider_payment_id=payment_id,
            source_platform=_metadata_platform(metadata),
        )
    except (RuntimeError, ValueError) as exc:
        log.exception("Gift paid mark failed for YooKassa payment_id=%s", payment_id)
        return f"gift_mark_failed:{type(exc).__name__}"
    return ""


def _grant_practices_if_needed(
    *,
    event: str,
    status: str,
    payment_id: str,
    user_id: int,
    metadata: dict[str, Any],
    amount_minor: int,
    currency: str,
) -> str:
    kind = str(metadata.get("kind") or "").strip().lower()
    package_id = str(metadata.get("package_id") or "").strip()
    succeeded = event == "payment.succeeded" or status == "succeeded"
    if not succeeded:
        return ""
    if kind not in {"tokens", "practices", "practice_package"} and not package_id:
        return ""
    if not user_id:
        return "missing_user_id_for_practice_grant"
    if not package_id:
        return "missing_package_id_for_practice_grant"
    payment_problem = _practice_package_payment_problem(
        package_id=package_id,
        amount_minor=amount_minor,
        currency=currency,
    )
    if payment_problem:
        return payment_problem
    gift_problem = _mark_paid_gift_if_needed(
        event=event,
        status=status,
        payment_id=payment_id,
        user_id=user_id,
        metadata=metadata,
        package_id=package_id,
    )
    if gift_problem:
        return gift_problem
    if normalize_gift_token(str(metadata.get("gift_token") or "")):
        log.info("Gift package paid; direct buyer grant skipped: payment_id=%s user_id=%s package_id=%s", payment_id, user_id, package_id)
        return ""
    try:
        inserted, wallet, _ledger_id = grant_tokens_for_payment(
            provider="yookassa",
            provider_payment_id=payment_id,
            user_id=int(user_id),
            package_id=package_id,
            source="yookassa_webhook",
        )
        premium = grant_premium_entitlements_for_payment(
            provider="yookassa",
            provider_payment_id=payment_id,
            user_id=int(user_id),
            package_id=package_id,
            source="yookassa_webhook",
            fallback_platform=_metadata_platform(metadata),
        )
    except (RuntimeError, ValueError) as exc:
        log.exception("Practice token or premium grant failed for YooKassa payment_id=%s", payment_id)
        return f"practice_grant_failed:{type(exc).__name__}"
    log.info(
        "Practice package processed: payment_id=%s user_id=%s package_id=%s inserted=%s balance=%s premium_outbox=%s consultation=%s",
        payment_id,
        user_id,
        package_id,
        inserted,
        wallet.available_tokens,
        premium.outbox_created,
        premium.consultation_request_created,
    )
    return ""


def record_yookassa_webhook(payload: dict[str, Any]) -> ReconciliationResult:
    """Record a YooKassa webhook as an idempotent provider-ledger fact.

    For practice package payments, payment.succeeded grants purchased practices
    through the single practice token ledger. Gift package payments are marked as
    paid and are granted to the recipient only when the gift token is claimed.
    Duplicate YooKassa webhooks are deduped by provider_payment_id.
    """
    event = str(payload.get("event") or "").strip()
    obj = payload.get("object") or {}
    if not isinstance(obj, dict):
        obj = {}

    payment_id = str(obj.get("id") or payload.get("id") or "").strip()
    status = str(obj.get("status") or "unknown").strip() or "unknown"
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    amount_obj = obj.get("amount") if isinstance(obj.get("amount"), dict) else None
    amount_minor = _amount_to_minor_units(amount_obj)
    currency = str((amount_obj or {}).get("currency") or "RUB").strip().upper()
    user_id = _metadata_user_id(metadata)
    kind = str((metadata or {}).get("kind") or "payment").strip() or "payment"

    if not payment_id:
        return ReconciliationResult(
            ok=False,
            provider="yookassa",
            provider_payment_id="",
            status=status,
            event=event,
            inserted=False,
            problem="missing_provider_payment_id",
        )

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:12000]
    provider_event_id = f"yookassa:{payment_id}:{event or status}"
    synthetic_charge_id = f"yookassa:{payment_id}"
    created_at = _utc_now_iso()
    problem = "" if user_id else "missing_user_id"

    grant_problem = _grant_practices_if_needed(
        event=event,
        status=status,
        payment_id=payment_id,
        user_id=int(user_id),
        metadata=metadata,
        amount_minor=amount_minor,
        currency=currency,
    )
    if grant_problem:
        problem = ";".join(item for item in (problem, grant_problem) if item)

    with db() as conn:
        with tx(conn):
            row = conn.execute(
                "SELECT id FROM payments WHERE provider_charge_id=? OR telegram_charge_id=? LIMIT 1",
                (payment_id, synthetic_charge_id),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE payments
                    SET provider_status=?, provider_event_id=?, provider_raw=?, reconciled_at=?, problem=?
                    WHERE provider_charge_id=? OR telegram_charge_id=?
                    """.strip(),
                    (status, provider_event_id, raw, created_at, problem, payment_id, synthetic_charge_id),
                )
                return ReconciliationResult(
                    ok=True,
                    provider="yookassa",
                    provider_payment_id=payment_id,
                    status=status,
                    event=event,
                    inserted=False,
                    problem=problem,
                )

            conn.execute(
                """
                INSERT INTO payments(
                    user_id, telegram_charge_id, provider_charge_id, payload,
                    amount, currency, created_at,
                    provider_status, provider_event_id, provider_raw, reconciled_at, problem
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """.strip(),
                (
                    int(user_id),
                    synthetic_charge_id,
                    payment_id,
                    f"yookassa:{kind}",
                    int(amount_minor),
                    currency,
                    created_at,
                    status,
                    provider_event_id,
                    raw,
                    created_at,
                    problem,
                ),
            )

    log.info(
        "YooKassa webhook reconciled: payment_id=%s status=%s event=%s user_id=%s problem=%s",
        payment_id,
        status,
        event,
        user_id,
        problem or "none",
    )
    return ReconciliationResult(
        ok=True,
        provider="yookassa",
        provider_payment_id=payment_id,
        status=status,
        event=event,
        inserted=True,
        problem=problem,
    )


def _row_to_payment_problem(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return {
        "id": row[0],
        "user_id": row[1],
        "provider_charge_id": row[2],
        "payload": row[3],
        "amount": row[4],
        "currency": row[5],
        "provider_status": row[6],
        "problem": row[7],
        "reconciled_at": row[8],
        "created_at": row[9],
    }


def payment_problem_summary(limit: int = 20, *, user_id: int | None = None) -> list[dict[str, Any]]:
    """Return recent payment records that need admin attention."""
    base_query = """
        SELECT id, user_id, provider_charge_id, payload, amount, currency,
               provider_status, problem, reconciled_at, created_at
        FROM payments
        WHERE (COALESCE(problem, '') <> ''
           OR provider_status IN ('canceled', 'waiting_for_capture'))
    """.strip()
    params: list[Any] = []
    if user_id is not None:
        base_query += " AND user_id=?"
        params.append(int(user_id))
    base_query += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))

    with db() as conn:
        rows = conn.execute(base_query, tuple(params)).fetchall()
    return [_row_to_payment_problem(row) for row in rows]
