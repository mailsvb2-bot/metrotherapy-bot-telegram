from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from core.time_utils import utc_now, utc_now_iso
from services.db import db, tx
from services.db.runtime import CONFIG
from services.payments.reconciliation import ReconciliationResult

log = logging.getLogger(__name__)
_PROVIDER = "yookassa"
_LOCAL_RETRYABLE_PREFIXES = (
    "practice_grant_failed:",
    "gift_mark_failed:",
)

_POSTGRES_CLAIM_SQL = """
WITH due AS (
    SELECT id
    FROM payment_reconciliation_retry
    WHERE (
        (status IN ('pending','retry') AND available_at<=?)
        OR (status='processing' AND locked_at IS NOT NULL AND locked_at<=?)
    )
    ORDER BY id ASC
    LIMIT ?
    FOR UPDATE SKIP LOCKED
)
UPDATE payment_reconciliation_retry
SET status='processing', locked_at=?, lock_token=?, updated_at=?
FROM due
WHERE payment_reconciliation_retry.id=due.id
RETURNING payment_reconciliation_retry.id,
          payment_reconciliation_retry.provider_payment_id,
          payment_reconciliation_retry.event,
          payment_reconciliation_retry.payload_json,
          payment_reconciliation_retry.attempts,
          payment_reconciliation_retry.lock_token
""".strip()

_SQLITE_SELECT_DUE_SQL = """
SELECT id
FROM payment_reconciliation_retry
WHERE (
    (status IN ('pending','retry') AND available_at<=?)
    OR (status='processing' AND locked_at IS NOT NULL AND locked_at<=?)
)
ORDER BY id ASC
LIMIT ?
""".strip()


@dataclass(frozen=True)
class ClaimedPaymentRetry:
    id: int
    provider_payment_id: str
    event: str
    payload_json: str
    attempts: int
    lock_token: str


@dataclass(frozen=True)
class PaymentRetryBatchResult:
    claimed: int = 0
    completed: int = 0
    rescheduled: int = 0
    dead: int = 0


def _row_value(row: Any, key: str, index: int) -> Any:
    return row[key] if hasattr(row, "keys") else row[index]


def _positive_int(name: str, default: int, *, minimum: int = 1, maximum: int = 86_400) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    return min(max(value, minimum), maximum)


def is_local_retryable_payment_problem(problem: str) -> bool:
    normalized = str(problem or "")
    return normalized.startswith(_LOCAL_RETRYABLE_PREFIXES)


def _payment_identity(payload: dict[str, Any], result: ReconciliationResult | None = None) -> tuple[str, str]:
    obj = payload.get("object")
    provider_object = obj if isinstance(obj, dict) else {}
    payment_id = str(
        provider_object.get("id")
        or (result.provider_payment_id if result is not None else "")
        or payload.get("id")
        or ""
    ).strip()
    event = str(payload.get("event") or (result.event if result is not None else "payment.unknown") or "payment.unknown").strip()
    return payment_id, event or "payment.unknown"


def _payment_user_id(payload: dict[str, Any]) -> int:
    obj = payload.get("object")
    provider_object = obj if isinstance(obj, dict) else {}
    metadata = provider_object.get("metadata")
    meta = metadata if isinstance(metadata, dict) else {}
    for key in ("external_user_id", "user_id", "telegram_user_id"):
        raw = str(meta.get(key) or "").strip()
        if not raw:
            continue
        try:
            parsed = int(raw, 10)
        except ValueError:
            continue
        if parsed > 0 and str(parsed) == raw:
            return parsed
    return 0


def _payment_user_id(payload: dict[str, Any]) -> int:
    obj = payload.get("object")
    provider_object = obj if isinstance(obj, dict) else {}
    metadata = provider_object.get("metadata")
    meta = metadata if isinstance(metadata, dict) else {}
    for key in ("external_user_id", "user_id", "telegram_user_id"):
        raw = str(meta.get(key) or "").strip()
        if not raw:
            continue
        try:
            parsed = int(raw, 10)
        except ValueError:
            continue
        if parsed > 0 and str(parsed) == raw:
            return parsed
    return 0


def enqueue_verified_payment_retry(payload: dict[str, Any], result: ReconciliationResult) -> bool:
    """Persist a provider-verified payment event for local side-effect replay."""

    if not is_local_retryable_payment_problem(result.problem):
        return False
    payment_id, event = _payment_identity(payload, result)
    if not payment_id:
        raise ValueError("payment retry requires provider_payment_id")

    now = utc_now_iso()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with db() as conn:
        with tx(conn):
            cursor = conn.execute(
                """
                INSERT INTO payment_reconciliation_retry(
                    provider,provider_payment_id,user_id,event,payload_json,status,attempts,
                    available_at,locked_at,lock_token,last_error,created_at,updated_at,completed_at
                ) VALUES(?,?,?,?,?,'pending',0,?,NULL,NULL,?,?,?,NULL)
                ON CONFLICT(provider,provider_payment_id,event) DO UPDATE SET
                    user_id=CASE
                        WHEN payment_reconciliation_retry.user_id<>0
                        THEN payment_reconciliation_retry.user_id
                        ELSE excluded.user_id
                    END,
                    payload_json=excluded.payload_json,
                    status=CASE
                        WHEN payment_reconciliation_retry.status IN ('completed','dead')
                        THEN payment_reconciliation_retry.status
                        ELSE 'retry'
                    END,
                    available_at=CASE
                        WHEN payment_reconciliation_retry.status IN ('completed','dead')
                        THEN payment_reconciliation_retry.available_at
                        ELSE excluded.available_at
                    END,
                    locked_at=CASE
                        WHEN payment_reconciliation_retry.status IN ('completed','dead')
                        THEN payment_reconciliation_retry.locked_at
                        ELSE NULL
                    END,
                    lock_token=CASE
                        WHEN payment_reconciliation_retry.status IN ('completed','dead')
                        THEN payment_reconciliation_retry.lock_token
                        ELSE NULL
                    END,
                    last_error=CASE
                        WHEN payment_reconciliation_retry.status='completed'
                        THEN payment_reconciliation_retry.last_error
                        ELSE excluded.last_error
                    END,
                    updated_at=excluded.updated_at
                """.strip(),
                (
                    _PROVIDER,
                    payment_id,
                    _payment_user_id(payload),
                    event,
                    encoded,
                    now,
                    str(result.problem or "")[:500],
                    now,
                    now,
                ),
            )
            return int(getattr(cursor, "rowcount", 0) or 0) > 0


def complete_payment_retry_if_present(payload: dict[str, Any], result: ReconciliationResult) -> None:
    payment_id, event = _payment_identity(payload, result)
    if not payment_id:
        return
    now = utc_now_iso()
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                UPDATE payment_reconciliation_retry
                SET status='completed',completed_at=COALESCE(completed_at,?),updated_at=?,
                    locked_at=NULL,lock_token=NULL,last_error=''
                WHERE provider=? AND provider_payment_id=? AND event=? AND status<>'dead'
                """.strip(),
                (now, now, _PROVIDER, payment_id, event),
            )


def _claimed_from_rows(rows: list[Any], token: str) -> list[ClaimedPaymentRetry]:
    return [
        ClaimedPaymentRetry(
            id=int(_row_value(row, "id", 0)),
            provider_payment_id=str(_row_value(row, "provider_payment_id", 1)),
            event=str(_row_value(row, "event", 2)),
            payload_json=str(_row_value(row, "payload_json", 3)),
            attempts=int(_row_value(row, "attempts", 4) or 0),
            lock_token=str(_row_value(row, "lock_token", 5) or token),
        )
        for row in rows
    ]


def claim_due_payment_retries(*, limit: int = 10, lock_ttl_sec: int | None = None) -> list[ClaimedPaymentRetry]:
    now = utc_now().replace(microsecond=0)
    now_iso = now.isoformat()
    ttl = lock_ttl_sec or _positive_int("PAYMENT_RETRY_LOCK_TTL_SEC", 900, minimum=30, maximum=86_400)
    stale_before = (now - timedelta(seconds=int(ttl))).isoformat()
    token = uuid.uuid4().hex
    bounded_limit = max(1, min(int(limit), 100))

    with db() as conn:
        with tx(conn):
            if CONFIG.uses_postgres:
                rows = conn.execute(
                    _POSTGRES_CLAIM_SQL,
                    (now_iso, stale_before, bounded_limit, now_iso, token, now_iso),
                ).fetchall()
                return _claimed_from_rows(list(rows), token)

            rows = conn.execute(
                _SQLITE_SELECT_DUE_SQL,
                (now_iso, stale_before, bounded_limit),
            ).fetchall()
            ids = [int(_row_value(row, "id", 0)) for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                "UPDATE payment_reconciliation_retry "
                "SET status='processing',locked_at=?,lock_token=?,updated_at=? "
                f"WHERE id IN ({placeholders}) AND "  # nosec B608 - placeholders are generated from integer IDs
                "((status IN ('pending','retry') AND available_at<=?) "
                "OR (status='processing' AND locked_at IS NOT NULL AND locked_at<=?))",
                [now_iso, token, now_iso, *ids, now_iso, stale_before],
            )
            claimed = conn.execute(
                "SELECT id,provider_payment_id,event,payload_json,attempts,lock_token "
                "FROM payment_reconciliation_retry WHERE lock_token=? "
                f"AND id IN ({placeholders}) ORDER BY id",  # nosec B608 - placeholders are generated from integer IDs
                [token, *ids],
            ).fetchall()
            return _claimed_from_rows(list(claimed), token)


def _mark_completed(item: ClaimedPaymentRetry) -> None:
    now = utc_now_iso()
    with db() as conn:
        with tx(conn):
            cursor = conn.execute(
                """
                UPDATE payment_reconciliation_retry
                SET status='completed',completed_at=COALESCE(completed_at,?),updated_at=?,
                    locked_at=NULL,lock_token=NULL,last_error=''
                WHERE id=? AND lock_token=? AND status='processing'
                """.strip(),
                (now, now, int(item.id), item.lock_token),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise RuntimeError("payment_retry_lease_lost")


def _mark_dead(item: ClaimedPaymentRetry, error: str) -> None:
    now = utc_now_iso()
    with db() as conn:
        with tx(conn):
            cursor = conn.execute(
                """
                UPDATE payment_reconciliation_retry
                SET status='dead',attempts=?,updated_at=?,locked_at=NULL,lock_token=NULL,last_error=?
                WHERE id=? AND lock_token=? AND status='processing'
                """.strip(),
                (
                    int(item.attempts) + 1,
                    now,
                    str(error or "non_retryable_result")[:500],
                    int(item.id),
                    item.lock_token,
                ),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise RuntimeError("payment_retry_lease_lost")
    log.error(
        "Payment reconciliation retry permanently failed: payment_id=%s error=%s",
        item.provider_payment_id,
        str(error or "")[:180],
    )


def _mark_dead(item: ClaimedPaymentRetry, error: str) -> None:
    now = utc_now_iso()
    with db() as conn:
        with tx(conn):
            cursor = conn.execute(
                """
                UPDATE payment_reconciliation_retry
                SET status='dead',attempts=?,updated_at=?,locked_at=NULL,lock_token=NULL,last_error=?
                WHERE id=? AND lock_token=? AND status='processing'
                """.strip(),
                (
                    int(item.attempts) + 1,
                    now,
                    str(error or "non_retryable_result")[:500],
                    int(item.id),
                    item.lock_token,
                ),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise RuntimeError("payment_retry_lease_lost")
    log.error(
        "Payment reconciliation retry permanently failed: payment_id=%s error=%s",
        item.provider_payment_id,
        str(error or "")[:180],
    )


def _reschedule_or_dead(item: ClaimedPaymentRetry, error: str) -> bool:
    attempts = int(item.attempts) + 1
    max_attempts = _positive_int("PAYMENT_RETRY_MAX_ATTEMPTS", 48, minimum=1, maximum=200)
    terminal = attempts >= max_attempts
    now = utc_now().replace(microsecond=0)
    base_delay = _positive_int("PAYMENT_RETRY_BASE_DELAY_SEC", 30, minimum=1, maximum=3600)
    max_delay = _positive_int("PAYMENT_RETRY_MAX_DELAY_SEC", 3600, minimum=30, maximum=86_400)
    delay = min(base_delay * (2 ** max(0, attempts - 1)), max_delay)
    available_at = (now + timedelta(seconds=delay)).isoformat()
    status = "dead" if terminal else "retry"
    with db() as conn:
        with tx(conn):
            cursor = conn.execute(
                """
                UPDATE payment_reconciliation_retry
                SET status=?,attempts=?,available_at=?,updated_at=?,locked_at=NULL,lock_token=NULL,last_error=?
                WHERE id=? AND lock_token=? AND status='processing'
                """.strip(),
                (
                    status,
                    attempts,
                    available_at,
                    now.isoformat(),
                    str(error or "")[:500],
                    int(item.id),
                    item.lock_token,
                ),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise RuntimeError("payment_retry_lease_lost")
    if terminal:
        log.error(
            "Payment reconciliation retry dead-lettered: payment_id=%s attempts=%s error=%s",
            item.provider_payment_id,
            attempts,
            str(error or "")[:180],
        )
    return terminal


def _process_claimed_retry(item: ClaimedPaymentRetry) -> str:
    loaded = json.loads(item.payload_json)
    if not isinstance(loaded, dict):
        raise ValueError("payment retry payload must be an object")

    # Import lazily to keep verified_reconciliation -> retry_queue dependency acyclic.
    from services.payments.reconciliation import record_yookassa_webhook

    result = record_yookassa_webhook(loaded)
    if is_local_retryable_payment_problem(result.problem):
        dead = _reschedule_or_dead(item, result.problem)
        return "dead" if dead else "rescheduled"
    if not result.ok or result.problem:
        _mark_dead(item, result.problem or "non_retryable_reconciliation_result")
        return "dead"
    _mark_completed(item)
    return "completed"


def run_payment_retry_batch(*, limit: int | None = None) -> PaymentRetryBatchResult:
    batch_limit = limit or _positive_int("PAYMENT_RETRY_BATCH_SIZE", 10, minimum=1, maximum=100)
    claimed = claim_due_payment_retries(limit=batch_limit)
    completed = 0
    rescheduled = 0
    dead = 0
    for item in claimed:
        try:
            outcome = _process_claimed_retry(item)
        except Exception as exc:  # validator: allow-wide-except - durable worker boundary
            log.exception("Payment retry processing crashed: payment_id=%s", item.provider_payment_id)
            outcome = "dead" if _reschedule_or_dead(item, f"worker_exception:{type(exc).__name__}:{exc}") else "rescheduled"
        if outcome == "completed":
            completed += 1
        elif outcome == "dead":
            dead += 1
        else:
            rescheduled += 1
    return PaymentRetryBatchResult(
        claimed=len(claimed),
        completed=completed,
        rescheduled=rescheduled,
        dead=dead,
    )


def payment_retry_health_snapshot() -> dict[str, int]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status IN ('pending','retry','processing') THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status='dead' THEN 1 ELSE 0 END) AS dead
            FROM payment_reconciliation_retry
            """.strip()
        ).fetchone()
    if row is None:
        return {"payment_retry_active": 0, "payment_retry_dead": 0}
    return {
        "payment_retry_active": int(_row_value(row, "active", 0) or 0),
        "payment_retry_dead": int(_row_value(row, "dead", 1) or 0),
    }
