from __future__ import annotations

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

from services.db import db  # noqa: E402
from services.payments.reconciliation import record_yookassa_webhook  # noqa: E402
from services.practice_token_contract import package_by_id  # noqa: E402


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


def _count(conn, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _wallet(conn, user_id: int) -> int:
    row = conn.execute("SELECT available_tokens FROM practice_wallets WHERE user_id=?", (user_id,)).fetchone()
    return int(row[0]) if row else 0


def _snapshot(*, user_id: int, payment_id: str) -> dict[str, int]:
    with db() as conn:
        return {
            "wallet": _wallet(conn, user_id),
            "payments": _count(conn, "SELECT COUNT(*) FROM payments WHERE provider_charge_id=?", (payment_id,)),
            "payment_grants": _count(conn, "SELECT COUNT(*) FROM payment_token_grants WHERE provider=? AND provider_payment_id=?", ("yookassa", payment_id)),
            "entitlements": _count(conn, "SELECT COUNT(*) FROM premium_entitlements WHERE provider=? AND provider_payment_id=?", ("yookassa", payment_id)),
            "outbox": _count(conn, "SELECT COUNT(*) FROM premium_delivery_outbox WHERE idempotency_key LIKE ?", (f"%{payment_id}%",)),
            "consultation": _count(conn, "SELECT COUNT(*) FROM consultation_requests WHERE provider=? AND provider_payment_id=?", ("yookassa", payment_id)),
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
                "user_id": str(user_id),
                "external_user_id": str(user_id),
                "source": source,
                "kind": "tokens",
                "package_id": package_id,
            },
        },
    }


def _diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: after.get(key, 0) - before.get(key, 0) for key in set(before) | set(after)}


def probe(*, package_id: str, user_id: int, source: str, apply: bool, duplicate: bool) -> LiveReconciliationProbeResult:
    package = package_by_id(package_id)
    amount = f"{package.price_rub}.00"
    payment_id = f"probe-{package_id}-{uuid.uuid4().hex[:12]}"
    before = _snapshot(user_id=user_id, payment_id=payment_id)
    if not apply:
        return LiveReconciliationProbeResult(
            payment_id=payment_id,
            package_id=package_id,
            user_id=user_id,
            amount=amount,
            applied=False,
            first_ok=True,
            first_inserted=False,
            first_problem="dry_run",
        )

    first = record_yookassa_webhook(_payload(payment_id=payment_id, user_id=user_id, source=source, package_id=package_id, amount=amount))
    second = None
    if duplicate:
        second = record_yookassa_webhook(_payload(payment_id=payment_id, user_id=user_id, source=source, package_id=package_id, amount=amount))
    after = _snapshot(user_id=user_id, payment_id=payment_id)
    delta = _diff(before, after)
    return LiveReconciliationProbeResult(
        payment_id=payment_id,
        package_id=package_id,
        user_id=user_id,
        amount=amount,
        applied=True,
        first_ok=first.ok,
        first_inserted=first.inserted,
        first_problem=first.problem,
        second_ok=None if second is None else second.ok,
        second_inserted=None if second is None else second.inserted,
        second_problem=None if second is None else second.problem,
        wallet_delta=delta["wallet"],
        grant_rows_delta=delta["payment_grants"],
        payment_rows_delta=delta["payments"],
        entitlement_rows_delta=delta["entitlements"],
        outbox_rows_delta=delta["outbox"],
        consultation_rows_delta=delta["consultation"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", action="append", default=["practice_60"])
    parser.add_argument("--user-id", type=int, default=990000001)
    parser.add_argument("--source", default="telegram")
    parser.add_argument("--apply-webhooks", action="store_true")
    parser.add_argument("--allow-live-db-mutation", action="store_true")
    parser.add_argument("--duplicate", action="store_true")
    args = parser.parse_args()

    apply = bool(args.apply_webhooks and args.allow_live_db_mutation)
    results = [
        probe(package_id=package_id, user_id=args.user_id, source=args.source, apply=apply, duplicate=args.duplicate)
        for package_id in args.package
    ]
    report = {
        "ok": all(item.first_ok and item.first_problem in {"", "dry_run"} and (item.second_inserted is not True) for item in results),
        "applied": apply,
        "results": [asdict(item) for item in results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
