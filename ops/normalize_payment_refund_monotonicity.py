from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "services" / "payments" / "reconciliation.py"


def replace_region(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + replacement + text[end:]


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    state_region = '''@dataclass(frozen=True)
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


@dataclass(frozen=True)
class PaymentFactWriteResult:
    inserted: bool
    preserved_refund: PaymentLedgerState | None = None


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


def _preserved_refund_result(
    *,
    payment_id: str,
    event: str,
    incoming_status: str,
    state: PaymentLedgerState,
) -> ReconciliationResult:
    log.info(
        "YooKassa payment event preserved refund state: payment_id=%s incoming_status=%s local_status=%s processing_status=%s",
        payment_id,
        incoming_status,
        state.provider_status,
        state.processing_status,
    )
    return ReconciliationResult(
        ok=True,
        provider="yookassa",
        provider_payment_id=payment_id,
        status=state.provider_status or incoming_status,
        event=event,
        inserted=False,
        problem=state.problem,
        processing_status=state.processing_status,
        side_effects_done=state.processing_status == "refunded",
    )


'''
    text = replace_region(
        text,
        "@dataclass(frozen=True)\nclass ReconciliationResult:",
        "def _is_succeeded_payment(",
        state_region,
    )

    write_region = '''def _record_payment_fact(
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
) -> PaymentFactWriteResult:
    """Insert/update the provider payment ledger before side-effect grants.

    Refund processing is monotonic. Once the local ledger enters a completed,
    partial or action-required refund state, a delayed payment event may refresh
    provider evidence but cannot overwrite refund status, problems or processing
    state. The returned preserved state lets the caller stop before any grant.
    """

    with db() as conn:
        with tx(conn):
            row = conn.execute(
                """
                SELECT id, provider_status, processing_status, problem, processing_error
                FROM payments
                WHERE provider_charge_id=? OR telegram_charge_id=?
                LIMIT 1
                """.strip(),
                (payment_id, synthetic_charge_id),
            ).fetchone()
            if row:
                existing = PaymentLedgerState(
                    provider_status=str(_row_value(row, "provider_status", 1) or ""),
                    processing_status=str(_row_value(row, "processing_status", 2) or ""),
                    problem=str(_row_value(row, "problem", 3) or ""),
                    processing_error=str(_row_value(row, "processing_error", 4) or ""),
                )
                if _refund_state(existing.provider_status, existing.processing_status):
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
                    return PaymentFactWriteResult(inserted=False, preserved_refund=existing)

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
                return PaymentFactWriteResult(inserted=False)

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
            return PaymentFactWriteResult(inserted=True)


'''
    text = replace_region(
        text,
        "def _record_payment_fact(",
        "def record_yookassa_webhook(",
        write_region,
    )

    record_start = text.index("def record_yookassa_webhook(")
    block_start = text.index("    created_at = _utc_now_iso()\n", record_start)
    block_end = text.index("    grant_problem = _grant_practices_if_needed(\n", block_start)
    record_region = '''    created_at = _utc_now_iso()
    preliminary_problem = "" if user_id else "missing_user_id"
    processing_status = _initial_processing_status(event=event, status=status, metadata=metadata)

    fact_write = _record_payment_fact(
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
    if fact_write.preserved_refund is not None:
        return _preserved_refund_result(
            payment_id=payment_id,
            event=event,
            incoming_status=status,
            state=fact_write.preserved_refund,
        )
    inserted = fact_write.inserted

'''
    text = text[:block_start] + record_region + text[block_end:]

    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
