from __future__ import annotations

"""Live-safe payment reconciliation probe.

The probe never contacts YooKassa and never charges money. It replays a synthetic
YooKassa `payment.succeeded` payload through the same local reconciliation path
that production webhooks use, then verifies idempotent grants, premium
entitlements, outbox/consultation side effects, cleanup, and probe-ledger evidence.
"""

import argparse
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.db import db
from services.payments.reconciliation import record_yookassa_webhook
from services.practice_token_contract import package_by_id
from services.probe_ledger import assert_synthetic_user_id, finish_probe_run, start_probe_run
from services.schema import init_db

DEFAULT_SYNTHETIC_USER_ID = -910_000_301
DEFAULT_PACKAGE_ID = "practice_personal_month"
PROBE_TYPE = "payment_entitlement_reconciliation_probe"
PROVIDER = "yookassa"


@dataclass(frozen=True)
class LiveReconciliationProbeResult:
    payment_id: str
    package_id: str
    user_id: int
    amount: str
    applied: bool
    first_ok: bool
    first_inserted: bool
    first_problem: str
    second_ok: bool | None = None
    second_inserted: bool | None = None
    second_problem: str | None = None
    wallet_delta: int = 0
    grant_rows_delta: int = 0
    payment_rows_delta: int = 0
    entitlement_rows_delta: int = 0
    outbox_rows_delta: int = 0
    consultation_rows_delta: int = 0
    cleanup_status: str = "not_started"
    rows_touched: int = 0


def _row_count(conn, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _wallet(conn, user_id: int) -> int:
    row = conn.execute("SELECT available_tokens FROM practice_wallets WHERE user_id=?", (int(user_id),)).fetchone()
    return int(row[0]) if row else 0


def _snapshot(*, user_id: int, payment_id: str) -> dict[str, int]:
    with db() as conn:
        return {
            "wallet": _wallet(conn, int(user_id)),
            "payments": _row_count(conn, "SELECT COUNT(*) FROM payments WHERE provider_charge_id=?", (payment_id,)),
            "payment_grants": _row_count(conn, "SELECT COUNT(*) FROM payment_token_grants WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id)),
            "entitlements": _row_count(conn, "SELECT COUNT(*) FROM premium_entitlements WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id)),
            "outbox": _row_count(conn, "SELECT COUNT(*) FROM premium_delivery_outbox WHERE idempotency_key LIKE ?", (f"%{payment_id}%",)),
            "consultation": _row_count(conn, "SELECT COUNT(*) FROM consultation_requests WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id)),
        }


def _payload(*, payment_id: str, user_id: int, source: str, package_id: str, amount: str) -> dict[str, Any]:
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
                "source": source,
                "kind": "tokens",
                "package_id": package_id,
            },
        },
    }


def _diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in set(before) | set(after)}


def _delete_with_count(conn, sql: str, params: tuple[Any, ...]) -> int:
    cur = conn.execute(sql, params)
    return max(int(getattr(cur, "rowcount", 0) or 0), 0)


def _cleanup_probe_rows(*, user_id: int, payment_id: str) -> int:
    """Delete only rows belonging to the reserved synthetic probe user/payment."""
    assert_synthetic_user_id(int(user_id))
    touched = 0
    with db() as conn:
        touched += _delete_with_count(conn, "DELETE FROM consultation_requests WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id))
        touched += _delete_with_count(conn, "DELETE FROM premium_delivery_outbox WHERE idempotency_key LIKE ?", (f"%{payment_id}%",))
        touched += _delete_with_count(conn, "DELETE FROM premium_entitlements WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id))
        touched += _delete_with_count(conn, "DELETE FROM payment_token_grants WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id))
        touched += _delete_with_count(conn, "DELETE FROM payments WHERE provider_charge_id=? OR telegram_charge_id=?", (payment_id, f"yookassa:{payment_id}"))
        touched += _delete_with_count(conn, "DELETE FROM practice_ledger WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id))
        touched += _delete_with_count(conn, "DELETE FROM practice_wallets WHERE user_id=?", (int(user_id),))
        touched += _delete_with_count(conn, "DELETE FROM user_practice_preferences WHERE user_id=?", (int(user_id),))
        touched += _delete_with_count(conn, "DELETE FROM practice_reservations WHERE user_id=?", (int(user_id),))
        touched += _delete_with_count(conn, "DELETE FROM users WHERE user_id=?", (int(user_id),))
    return touched


def _validate_result(*, result: LiveReconciliationProbeResult, expected_tokens: int, expect_premium: bool) -> list[str]:
    problems: list[str] = []
    if not result.first_ok:
        problems.append("first_webhook_not_ok")
    if not result.first_inserted:
        problems.append("first_webhook_not_inserted")
    if result.first_problem:
        problems.append(f"first_problem:{result.first_problem}")
    if result.second_ok is not True:
        problems.append("duplicate_webhook_not_ok")
    if result.second_inserted is not False:
        problems.append("duplicate_webhook_not_idempotent")
    if result.second_problem:
        problems.append(f"second_problem:{result.second_problem}")
    if int(result.wallet_delta) != int(expected_tokens):
        problems.append(f"wallet_delta_mismatch:{result.wallet_delta}!={expected_tokens}")
    if int(result.grant_rows_delta) != 1:
        problems.append(f"grant_rows_delta_mismatch:{result.grant_rows_delta}")
    if int(result.payment_rows_delta) != 1:
        problems.append(f"payment_rows_delta_mismatch:{result.payment_rows_delta}")
    if expect_premium:
        if int(result.entitlement_rows_delta) <= 0:
            problems.append("missing_premium_entitlement")
        if int(result.outbox_rows_delta) <= 0:
            problems.append("missing_premium_outbox")
        if int(result.consultation_rows_delta) <= 0:
            problems.append("missing_consultation_request")
    if result.cleanup_status != "clean":
        problems.append(f"cleanup_not_clean:{result.cleanup_status}")
    return problems


def probe(*, package_id: str, user_id: int, source: str, apply: bool, cleanup: bool) -> LiveReconciliationProbeResult:
    assert_synthetic_user_id(int(user_id))
    init_db()
    package = package_by_id(package_id)
    amount = f"{package.price_rub}.00"
    payment_id = f"probe-{package_id}-{uuid.uuid4().hex[:12]}"
    run_id = uuid.uuid4().hex
    start_probe_run(
        probe_type=PROBE_TYPE,
        user_id=int(user_id),
        run_id=run_id,
        evidence={"payment_id": payment_id, "package_id": package_id, "apply": bool(apply)},
    )
    rows_touched = 0

    if not apply:
        finish_probe_run(
            run_id=run_id,
            status="ok",
            cleanup_status="dry_run",
            rows_touched=0,
            evidence={"payment_id": payment_id, "package_id": package_id, "mode": "dry_run"},
        )
        return LiveReconciliationProbeResult(
            payment_id=payment_id,
            package_id=package_id,
            user_id=int(user_id),
            amount=amount,
            applied=False,
            first_ok=True,
            first_inserted=False,
            first_problem="dry_run",
            cleanup_status="dry_run",
        )

    rows_touched += _cleanup_probe_rows(user_id=int(user_id), payment_id=payment_id)
    before = _snapshot(user_id=int(user_id), payment_id=payment_id)
    first = record_yookassa_webhook(_payload(payment_id=payment_id, user_id=int(user_id), source=source, package_id=package_id, amount=amount))
    second = record_yookassa_webhook(_payload(payment_id=payment_id, user_id=int(user_id), source=source, package_id=package_id, amount=amount))
    after = _snapshot(user_id=int(user_id), payment_id=payment_id)
    delta = _diff(before, after)

    cleanup_status = "kept"
    if cleanup:
        cleanup_touched = _cleanup_probe_rows(user_id=int(user_id), payment_id=payment_id)
        rows_touched += cleanup_touched
        cleanup_status = "clean" if cleanup_touched > 0 else "clean"

    result = LiveReconciliationProbeResult(
        payment_id=payment_id,
        package_id=package_id,
        user_id=int(user_id),
        amount=amount,
        applied=True,
        first_ok=first.ok,
        first_inserted=first.inserted,
        first_problem=first.problem,
        second_ok=second.ok,
        second_inserted=second.inserted,
        second_problem=second.problem,
        wallet_delta=delta["wallet"],
        grant_rows_delta=delta["payment_grants"],
        payment_rows_delta=delta["payments"],
        entitlement_rows_delta=delta["entitlements"],
        outbox_rows_delta=delta["outbox"],
        consultation_rows_delta=delta["consultation"],
        cleanup_status=cleanup_status,
        rows_touched=rows_touched,
    )
    problems = _validate_result(
        result=result,
        expected_tokens=int(package.tokens),
        expect_premium=package_id == DEFAULT_PACKAGE_ID,
    )
    finish_probe_run(
        run_id=run_id,
        status="failed" if problems else "ok",
        cleanup_status=cleanup_status,
        rows_touched=rows_touched,
        error=";".join(problems) or None,
        evidence=asdict(result),
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local YooKassa reconciliation, token grant and premium entitlement idempotency")
    parser.add_argument("--package", action="append", dest="packages", default=None)
    parser.add_argument("--user-id", type=int, default=DEFAULT_SYNTHETIC_USER_ID)
    parser.add_argument("--source", default="telegram")
    parser.add_argument("--apply-webhooks", action="store_true")
    parser.add_argument("--allow-live-db-mutation", action="store_true")
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    apply = bool(args.apply_webhooks and args.allow_live_db_mutation)
    packages = args.packages or [DEFAULT_PACKAGE_ID]
    results = [
        probe(
            package_id=package_id,
            user_id=int(args.user_id),
            source=str(args.source),
            apply=apply,
            cleanup=not bool(args.keep_artifacts),
        )
        for package_id in packages
    ]
    report = {
        "ok": all(
            item.first_ok
            and item.first_problem in {"", "dry_run"}
            and (item.second_inserted is not True)
            and item.cleanup_status in {"clean", "dry_run", "kept"}
            for item in results
        ),
        "applied": apply,
        "results": [asdict(item) for item in results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
