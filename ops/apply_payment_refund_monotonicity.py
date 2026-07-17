from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "services" / "payments" / "reconciliation.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count == 0 and new in text:
        return text
    if count != 1:
        raise SystemExit(f"expected exactly one {label} target, got {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '''@dataclass(frozen=True)
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


''',
        '''@dataclass(frozen=True)
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


''',
        label="ledger state dataclass",
    )

    text = replace_once(
        text,
        '''def _problem_join(*items: str) -> str:
    return ";".join(item for item in items if item)


''',
        '''def _problem_join(*items: str) -> str:
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


''',
        label="refund state helpers",
    )

    text = replace_once(
        text,
        '''            row = conn.execute(
                "SELECT id FROM payments WHERE provider_charge_id=? OR telegram_charge_id=? LIMIT 1",
                (payment_id, synthetic_charge_id),
            ).fetchone()
            if row:
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
''',
        '''            row = conn.execute(
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
''',
        label="payment update monotonicity",
    )

    text = replace_once(
        text,
        '''    created_at = _utc_now_iso()
    preliminary_problem = "" if user_id else "missing_user_id"
    processing_status = _initial_processing_status(event=event, status=status, metadata=metadata)

    inserted = _record_payment_fact(
''',
        '''    created_at = _utc_now_iso()
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
''',
        label="late payment refund short circuit",
    )

    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
