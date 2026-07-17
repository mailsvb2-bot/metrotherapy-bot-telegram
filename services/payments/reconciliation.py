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

_GRANT_KINDS = {"tokens", "practices", "practice_package"}
_WAITING_PROVIDER_STATUSES = {"pending", "waiting_for_capture"}
_TERMINAL_BAD_PROVIDER_STATUSES = {"canceled", "cancelled", "failed", "refunded"}


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
        if not value:
            continue
        try:
            parsed = int(value, 10)
        except ValueError:
            continue
        if str(parsed) == value and parsed != 0:
            return parsed
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
    processing_status: str = ""
    side_effects_done: bool = False


@dataclass(frozen=True)
class PaymentLedgerState:
    provider_status: str
    processing_status: str
    problem: str
    processing_error: str


def _problem_join(*items: str) -> str:
    return ";".join(item for item in items if item)


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


def _refund_state(provider_status: str, processing_status: str) -> bool:
    provider = str(provider_status or "").strip().lower()
    processing = str(processing_status or "").strip().lower()
    return provider == "refunded" or processing == "refunded" or processing.startswith("refund_")


def _existing_refund_state(payment_id: str, synthetic_charge_id: str) -> PaymentLedgerState | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT provider_status, processing_status, problem, processing_error
            FROM payments
            WHERE provider_charge_id=? OR telegram_charge_id=?
            LIMIT 1
            """.strip(),
            (payment_id, synthetic_charge_id),
        ).fetchone()
    if row is None:
        return None
    state = PaymentLedgerState(
        provider_status=str(_row_value(row, "provider_status", 0) or ""),
        processing_status=str(_row_value(row, "processing_status", 1) or ""),
        problem=str(_row_value(row, "problem", 2) or ""),
        processing_error=str(_row_value(row, "processing_error", 3) or ""),
    )
    return state if _refund_state(state.provider_status, state.processing_status) else None


def _is_succeeded_payment(*, event: str, status: str) -> bool:
    return event == "payment.succeeded" or status == "succeeded"


def _is_grant_candidate(*, event: str, status: str, metadata: dict[str, Any]) -> bool:
    if not _is_succeeded_payment(event=event, status=status):
        return False
    kind = str(metadata.get("kind") or "").strip().lower()
    package_id = str(metadata.get("package_id") or "").strip()
    return kind in _GRANT_KINDS or bool(package_id)


def _initial_processing_status(*, event: str, status: str, metadata: dict[str, Any]) -> str:
    if _is_grant_candidate(event=event, status=status, metadata=metadata):
        return "grant_pending"
    normalized_status = str(status or "").strip().lower()
    if normalized_status in _WAITING_PROVIDER_STATUSES:
        return "provider_waiting"
    if normalized_status in _TERMINAL_BAD_PROVIDER_STATUSES:
        return "provider_terminal_problem"
    if _is_succeeded_payment(event=event, status=status):
        return "provider_succeeded"
    return "received"


def _final_processing_status(
    *,
    event: str,
    status: str,
    metadata: dict[str, Any],
    problem: str,
) -> str:
    if problem:
        return "action_required"
    if _is_grant_candidate(event=event, status=status, metadata=metadata):
        return "side_effects_done"
    normalized_status = str(status or "").strip().lower()
    if normalized_status in _WAITING_PROVIDER_STATUSES:
        return "provider_waiting"
    if normalized_status in _TERMINAL_BAD_PROVIDER_STATUSES:
        return "provider_terminal_problem"
    if _is_succeeded_payment(event=event, status=status):
        return "provider_succeeded"
    return "received"


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
    succeeded = _is_succeeded_payment(event=event, status=status)
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
    succeeded = _is_succeeded_payment(event=event, status=status)
    if not succeeded:
        return ""
    if kind not in _GRANT_KINDS and not package_id:
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


def _record_payment_fact(
    *,
    payment_id: str,
    synthetic_charge_id: str,
    user_id: int,
    kind: str,
    amount_minor: int,
    currency: str,
    status: str,
    provider_event_id: str,
    raw: str,
    reconciled_at: str,
    problem: str,
    processing_status: str,
    granted_at_utc: str | None = None,
    side_effects_done_at_utc: str | None = None,
    processing_error: str = "",
) -> bool:
    """Insert/update the provider payment ledger fact before side-effect grants.

    Grants are separately idempotent and may use their own DB transactions. The
    payment fact must therefore exist first, so a process crash between fact and
    grant is recoverable by replaying the provider webhook. The local processing
    columns expose that resumable state to admin/reporting surfaces.
    """
    with db() as conn:
        with tx(conn):
            row = conn.execute(
                """
                SELECT id, provider_status, processing_status
                FROM payments
                WHERE provider_charge_id=? OR telegram_charge_id=?
                LIMIT 1
                """.strip(),
                (payment_id, synthetic_charge_id),
            ).fetchone()
            if row:
                existing_provider_status = str(_row_value(row, "provider_status", 1) or "")
                existing_processing_status = str(_row_value(row, "processing_status", 2) or "")
                if _refund_state(existing_provider_status, existing_processing_status):
                    conn.execute(
                        """
                        UPDATE payments
                        SET provider_event_id=?, provider_raw=?, reconciled_at=?
                        WHERE provider_charge_id=? OR telegram_charge_id=?
                        """.strip(),
                        (
                            provider_event_id,
                            raw,
                            reconciled_at,
                            payment_id,
                            synthetic_charge_id,
                        ),
                    )
                    return False
                conn.execute(
                    """
                    UPDATE payments
                    SET provider_status=?, provider_event_id=?, provider_raw=?, reconciled_at=?, problem=?,
                        processing_status=?,
                        granted_at_utc=COALESCE(granted_at_utc, ?),
                        side_effects_done_at_utc=COALESCE(side_effects_done_at_utc, ?),
                        processing_error=?
                    WHERE provider_charge_id=? OR telegram_charge_id=?
                    """.strip(),
                    (
                        status,
                        provider_event_id,
                        raw,
                        reconciled_at,
                        problem,
                        processing_status,
                        granted_at_utc,
                        side_effects_done_at_utc,
                        processing_error,
                        payment_id,
                        synthetic_charge_id,
                    ),
                )
                return False

            conn.execute(
                """
                INSERT INTO payments(
                    user_id, telegram_charge_id, provider_charge_id, payload,
                    amount, currency, created_at,
                    provider_status, provider_event_id, provider_raw, reconciled_at, problem,
                    processing_status, granted_at_utc, side_effects_done_at_utc, processing_error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """.strip(),
                (
                    int(user_id),
                    synthetic_charge_id,
                    payment_id,
                    f"yookassa:{kind}",
                    int(amount_minor),
                    currency,
                    reconciled_at,
                    status,
                    provider_event_id,
                    raw,
                    reconciled_at,
                    problem,
                    processing_status,
                    granted_at_utc,
                    side_effects_done_at_utc,
                    processing_error,
                ),
            )
            return True


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
            processing_status="action_required",
            side_effects_done=False,
        )

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:12000]
    provider_event_id = f"yookassa:{payment_id}:{event or status}"
    synthetic_charge_id = f"yookassa:{payment_id}"
    created_at = _utc_now_iso()
    existing_refund = _existing_refund_state(payment_id, synthetic_charge_id)
    if existing_refund is not None:
        _record_payment_fact(
            payment_id=payment_id,
            synthetic_charge_id=synthetic_charge_id,
            user_id=int(user_id),
            kind=kind,
            amount_minor=amount_minor,
            currency=currency,
            status=status,
            provider_event_id=provider_event_id,
            raw=raw,
            reconciled_at=created_at,
            problem=existing_refund.problem,
            processing_status=existing_refund.processing_status,
            processing_error=existing_refund.processing_error,
        )
        log.info(
            "YooKassa payment event preserved refund state: payment_id=%s incoming_status=%s local_status=%s processing_status=%s",
            payment_id,
            status,
            existing_refund.provider_status,
            existing_refund.processing_status,
        )
        return ReconciliationResult(
            ok=True,
            provider="yookassa",
            provider_payment_id=payment_id,
            status=existing_refund.provider_status or status,
            event=event,
            inserted=False,
            problem=existing_refund.problem,
            processing_status=existing_refund.processing_status,
            side_effects_done=existing_refund.processing_status == "refunded",
        )

    preliminary_problem = "" if user_id else "missing_user_id"
    processing_status = _initial_processing_status(event=event, status=status, metadata=metadata)

    inserted = _record_payment_fact(
        payment_id=payment_id,
        synthetic_charge_id=synthetic_charge_id,
        user_id=int(user_id),
        kind=kind,
        amount_minor=amount_minor,
        currency=currency,
        status=status,
        provider_event_id=provider_event_id,
        raw=raw,
        reconciled_at=created_at,
        problem=preliminary_problem,
        processing_status=processing_status,
        processing_error=preliminary_problem,
    )

    grant_problem = _grant_practices_if_needed(
        event=event,
        status=status,
        payment_id=payment_id,
        user_id=int(user_id),
        metadata=metadata,
        amount_minor=amount_minor,
        currency=currency,
    )
    problem = _problem_join(preliminary_problem, grant_problem)
    final_processing_status = _final_processing_status(
        event=event,
        status=status,
        metadata=metadata,
        problem=problem,
    )
    side_effects_done = final_processing_status in {"side_effects_done", "provider_succeeded"}
    grant_candidate = _is_grant_candidate(event=event, status=status, metadata=metadata)
    granted_at = created_at if grant_candidate and not problem else None
    side_effects_done_at = created_at if side_effects_done else None

    if (
        problem != preliminary_problem
        or final_processing_status != processing_status
        or side_effects_done_at is not None
        or granted_at is not None
    ):
        _record_payment_fact(
            payment_id=payment_id,
            synthetic_charge_id=synthetic_charge_id,
            user_id=int(user_id),
            kind=kind,
            amount_minor=amount_minor,
            currency=currency,
            status=status,
            provider_event_id=provider_event_id,
            raw=raw,
            reconciled_at=created_at,
            problem=problem,
            processing_status=final_processing_status,
            granted_at_utc=granted_at,
            side_effects_done_at_utc=side_effects_done_at,
            processing_error=problem,
        )

    log.info(
        "YooKassa webhook reconciled: payment_id=%s status=%s event=%s user_id=%s processing_status=%s problem=%s",
        payment_id,
        status,
        event,
        user_id,
        final_processing_status,
        problem or "none",
    )
    return ReconciliationResult(
        ok=True,
        provider="yookassa",
        provider_payment_id=payment_id,
        status=status,
        event=event,
        inserted=inserted,
        problem=problem,
        processing_status=final_processing_status,
        side_effects_done=side_effects_done,
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
        "processing_status": row[10],
        "processing_error": row[11],
        "granted_at_utc": row[12],
        "side_effects_done_at_utc": row[13],
    }


def payment_problem_summary(limit: int = 20, *, user_id: int | None = None) -> list[dict[str, Any]]:
    """Return recent payment records that need admin attention."""
    base_query = """
        SELECT id, user_id, provider_charge_id, payload, amount, currency,
               provider_status, problem, reconciled_at, created_at,
               processing_status, processing_error, granted_at_utc, side_effects_done_at_utc
        FROM payments
        WHERE (COALESCE(problem, '') <> ''
           OR provider_status IN ('canceled', 'cancelled', 'failed', 'refunded', 'waiting_for_capture')
           OR COALESCE(processing_status, '') IN ('grant_pending', 'action_required', 'provider_waiting', 'provider_terminal_problem'))
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
