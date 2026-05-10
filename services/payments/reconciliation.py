from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.db import db, tx

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _amount_to_minor_units(amount: dict[str, Any] | None) -> int:
    if not amount:
        return 0
    raw = str(amount.get("value") or "0").replace(",", ".").strip()
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return 0


def _metadata_user_id(metadata: dict[str, Any] | None) -> int:
    if not metadata:
        return 0
    for key in ("external_user_id", "user_id", "telegram_user_id"):
        value = str(metadata.get(key) or "").strip()
        if value.isdigit():
            return int(value)
    return 0


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    provider: str
    provider_payment_id: str
    status: str
    event: str
    inserted: bool
    problem: str = ""


def record_yookassa_webhook(payload: dict[str, Any]) -> ReconciliationResult:
    """Record a YooKassa webhook as an idempotent provider-ledger fact.

    This intentionally does not grant access by itself. Access activation remains
    owned by the canonical Telegram successful_payment flow until a full web
    checkout-to-plan contract exists. The webhook is a reconciliation and support
    surface: it makes external provider state visible and dedupe-safe.
    """
    event = str(payload.get("event") or "").strip()
    obj = payload.get("object") or {}
    if not isinstance(obj, dict):
        obj = {}

    payment_id = str(obj.get("id") or payload.get("id") or "").strip()
    status = str(obj.get("status") or "unknown").strip() or "unknown"
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    amount_minor = _amount_to_minor_units(obj.get("amount") if isinstance(obj.get("amount"), dict) else None)
    currency = str(((obj.get("amount") or {}) if isinstance(obj.get("amount"), dict) else {}).get("currency") or "RUB").strip().upper()
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
                    SET provider_status=?, provider_event_id=?, provider_raw=?, reconciled_at=?, problem=COALESCE(NULLIF(problem,''), ?)
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


def payment_problem_summary(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent payment records that need admin attention."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, provider_charge_id, payload, amount, currency,
                   provider_status, problem, reconciled_at, created_at
            FROM payments
            WHERE COALESCE(problem, '') <> ''
               OR provider_status IN ('canceled', 'waiting_for_capture')
            ORDER BY id DESC
            LIMIT ?
            """.strip(),
            (int(limit),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            out.append({
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
            })
    return out
