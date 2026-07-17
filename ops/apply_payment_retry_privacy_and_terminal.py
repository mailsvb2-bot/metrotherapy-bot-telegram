from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "services" / "payments" / "retry_queue.py"
PRIVACY = ROOT / "services" / "privacy_manifest.py"


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0 and new in text:
        return
    if count != 1:
        raise SystemExit(f"expected exactly one {label} target in {path}, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    replace_once(
        QUEUE,
        '''def _payment_identity(payload: dict[str, Any], result: ReconciliationResult | None = None) -> tuple[str, str]:
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


''',
        '''def _payment_identity(payload: dict[str, Any], result: ReconciliationResult | None = None) -> tuple[str, str]:
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


''',
        label="payment retry user identity",
    )
    replace_once(
        QUEUE,
        '''                INSERT INTO payment_reconciliation_retry(
                    provider,provider_payment_id,event,payload_json,status,attempts,
                    available_at,locked_at,lock_token,last_error,created_at,updated_at,completed_at
                ) VALUES(?,?,?,?,'pending',0,?,NULL,NULL,?,?,?,NULL)
                ON CONFLICT(provider,provider_payment_id,event) DO UPDATE SET
                    payload_json=excluded.payload_json,
''',
        '''                INSERT INTO payment_reconciliation_retry(
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
''',
        label="payment retry insert ownership",
    )
    replace_once(
        QUEUE,
        '''                    _PROVIDER,
                    payment_id,
                    event,
                    encoded,
                    now,
''',
        '''                    _PROVIDER,
                    payment_id,
                    _payment_user_id(payload),
                    event,
                    encoded,
                    now,
''',
        label="payment retry insert values",
    )
    replace_once(
        QUEUE,
        '''def _reschedule_or_dead(item: ClaimedPaymentRetry, error: str) -> bool:
''',
        '''def _mark_dead(item: ClaimedPaymentRetry, error: str) -> None:
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
''',
        label="payment retry permanent dead function",
    )
    replace_once(
        QUEUE,
        '''    result = record_yookassa_webhook(loaded)
    if is_local_retryable_payment_problem(result.problem):
        dead = _reschedule_or_dead(item, result.problem)
        return "dead" if dead else "rescheduled"
    _mark_completed(item)
    return "completed"
''',
        '''    result = record_yookassa_webhook(loaded)
    if is_local_retryable_payment_problem(result.problem):
        dead = _reschedule_or_dead(item, result.problem)
        return "dead" if dead else "rescheduled"
    if not result.ok or result.problem:
        _mark_dead(item, result.problem or "non_retryable_reconciliation_result")
        return "dead"
    _mark_completed(item)
    return "completed"
''',
        label="payment retry terminal result handling",
    )
    replace_once(
        PRIVACY,
        'MANIFEST_VERSION = "2026-07-17.v2"\n',
        'MANIFEST_VERSION = "2026-07-17.v3"\n',
        label="privacy manifest version",
    )
    replace_once(
        PRIVACY,
        '''    ("payments", ("user_id",), "payment, refund, dispute and accounting fact", True),
    ("payment_events", ("user_id",), "provider payment idempotency fact", True),
''',
        '''    ("payments", ("user_id",), "payment, refund, dispute and accounting fact", True),
    ("payment_events", ("user_id",), "provider payment idempotency fact", True),
    (
        "payment_reconciliation_retry",
        ("user_id",),
        "provider-verified payment fulfilment retry and audit fact",
        True,
    ),
''',
        label="payment retry privacy policy",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
