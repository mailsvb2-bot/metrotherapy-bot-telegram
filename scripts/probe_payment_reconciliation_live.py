from __future__ import annotations

"""Live-safe local payment reconciliation probe.

The probe never contacts YooKassa and never charges money. Dry-run mode performs
no database initialization and writes no probe-ledger rows. Mutation mode replays
a synthetic YooKassa ``payment.succeeded`` payload through the same local
reconciliation path used by production webhooks, verifies duplicate-event
idempotency, and removes every synthetic account/payment artifact unless the
operator explicitly asks to keep it.
"""

import argparse
import json
import sqlite3
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
from services.probe_ledger import (
    SYNTHETIC_USER_ID_MAX,
    SYNTHETIC_USER_ID_MIN,
    assert_synthetic_user_id,
    finish_probe_run,
    start_probe_run,
)
from services.schema import init_db

DEFAULT_PACKAGE_ID = "practice_personal_month"
PROBE_TYPE = "payment_entitlement_reconciliation_probe"
PROVIDER = "yookassa"
PAYMENT_ID_PREFIX = "synthetic-probe-yookassa"
ALLOWED_SOURCES = frozenset({"telegram", "vk", "max"})


@dataclass(frozen=True)
class LiveReconciliationProbeResult:
    run_id: str
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
    account_rows_delta: int = 0
    identity_rows_delta: int = 0
    cleanup_status: str = "not_started"
    residual_rows: int = 0
    rows_touched: int = 0


def _new_synthetic_user_id() -> int:
    namespace_size = int(SYNTHETIC_USER_ID_MAX) - int(SYNTHETIC_USER_ID_MIN) + 1
    offset = int(uuid.uuid4().hex[:12], 16) % namespace_size
    user_id = int(SYNTHETIC_USER_ID_MAX) - offset
    assert_synthetic_user_id(user_id)
    return user_id


def _row_count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _wallet(conn: Any, user_id: int) -> int:
    row = conn.execute(
        "SELECT available_tokens FROM practice_wallets WHERE user_id=?",
        (int(user_id),),
    ).fetchone()
    return int(row[0]) if row else 0


def _outbox_prefix(payment_id: str) -> str:
    return f"premium_delivery:{PROVIDER}:{payment_id}:"


def _snapshot(*, user_id: int, payment_id: str) -> dict[str, int]:
    uid = int(user_id)
    external_uid = str(uid)
    outbox_prefix = _outbox_prefix(payment_id)
    with db() as conn:
        return {
            "wallet": _wallet(conn, uid),
            "users": _row_count(conn, "SELECT COUNT(*) FROM users WHERE user_id=?", (uid,)),
            "payments": _row_count(
                conn,
                "SELECT COUNT(*) FROM payments WHERE provider_charge_id=? OR telegram_charge_id=?",
                (payment_id, f"yookassa:{payment_id}"),
            ),
            "payment_grants": _row_count(
                conn,
                "SELECT COUNT(*) FROM payment_token_grants WHERE provider=? AND provider_payment_id=?",
                (PROVIDER, payment_id),
            ),
            "entitlements": _row_count(
                conn,
                "SELECT COUNT(*) FROM premium_entitlements WHERE provider=? AND provider_payment_id=?",
                (PROVIDER, payment_id),
            ),
            "outbox": _row_count(
                conn,
                "SELECT COUNT(*) FROM premium_delivery_outbox WHERE substr(idempotency_key, 1, ?)=?",
                (len(outbox_prefix), outbox_prefix),
            ),
            "consultation": _row_count(
                conn,
                "SELECT COUNT(*) FROM consultation_requests WHERE provider=? AND provider_payment_id=?",
                (PROVIDER, payment_id),
            ),
            "accounts": _row_count(
                conn,
                "SELECT COUNT(*) FROM accounts WHERE account_id=? OR primary_user_id=?",
                (uid, uid),
            ),
            "identities": _row_count(
                conn,
                "SELECT COUNT(*) FROM account_channel_identities WHERE account_id=? OR external_user_id=?",
                (uid, external_uid),
            ),
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
    return {
        key: int(after.get(key, 0)) - int(before.get(key, 0))
        for key in set(before) | set(after)
    }


def _delete_with_count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    cur = conn.execute(sql, params)
    return max(int(getattr(cur, "rowcount", 0) or 0), 0)


def _cleanup_probe_rows(*, user_id: int, payment_id: str) -> int:
    """Delete only rows belonging to a reserved synthetic user and payment."""

    assert_synthetic_user_id(int(user_id))
    uid = int(user_id)
    external_uid = str(uid)
    outbox_prefix = _outbox_prefix(payment_id)
    touched = 0
    with db() as conn:
        payment_statements = (
            (
                "DELETE FROM consultation_requests WHERE provider=? AND provider_payment_id=?",
                (PROVIDER, payment_id),
            ),
            (
                "DELETE FROM premium_delivery_outbox WHERE substr(idempotency_key, 1, ?)=?",
                (len(outbox_prefix), outbox_prefix),
            ),
            (
                "DELETE FROM premium_entitlements WHERE provider=? AND provider_payment_id=?",
                (PROVIDER, payment_id),
            ),
            (
                "DELETE FROM payment_token_grants WHERE provider=? AND provider_payment_id=?",
                (PROVIDER, payment_id),
            ),
            (
                "DELETE FROM payments WHERE provider_charge_id=? OR telegram_charge_id=?",
                (payment_id, f"yookassa:{payment_id}"),
            ),
            (
                "DELETE FROM practice_ledger WHERE provider=? AND provider_payment_id=?",
                (PROVIDER, payment_id),
            ),
        )
        user_statements = (
            ("DELETE FROM practice_reservations WHERE user_id=?", (uid,)),
            ("DELETE FROM practice_ledger WHERE user_id=?", (uid,)),
            ("DELETE FROM practice_wallets WHERE user_id=?", (uid,)),
            ("DELETE FROM user_practice_preferences WHERE user_id=?", (uid,)),
            ("DELETE FROM premium_delivery_outbox WHERE user_id=?", (uid,)),
            ("DELETE FROM premium_entitlements WHERE user_id=?", (uid,)),
            ("DELETE FROM consultation_requests WHERE user_id=?", (uid,)),
            ("DELETE FROM account_audio_completions WHERE account_id=?", (uid,)),
            ("DELETE FROM account_audio_deliveries WHERE account_id=?", (uid,)),
            ("DELETE FROM account_audio_progress WHERE account_id=?", (uid,)),
            (
                "DELETE FROM account_channel_identities WHERE account_id=? OR external_user_id=?",
                (uid, external_uid),
            ),
            ("DELETE FROM accounts WHERE account_id=? OR primary_user_id=?", (uid, uid)),
            ("DELETE FROM users WHERE user_id=?", (uid,)),
        )
        for sql, params in (*payment_statements, *user_statements):
            touched += _delete_with_count(conn, sql, params)
    return touched


def _residual_row_count(*, user_id: int, payment_id: str) -> int:
    snapshot = _snapshot(user_id=int(user_id), payment_id=payment_id)
    return sum(max(int(value), 0) for value in snapshot.values())


def _created_row_count(delta: dict[str, int]) -> int:
    row_keys = (
        "users",
        "payments",
        "payment_grants",
        "entitlements",
        "outbox",
        "consultation",
        "accounts",
        "identities",
    )
    return sum(max(int(delta.get(key, 0)), 0) for key in row_keys)


def _validate_result(
    *,
    result: LiveReconciliationProbeResult,
    expected_tokens: int,
    expect_premium: bool,
) -> list[str]:
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
        if int(result.account_rows_delta) <= 0:
            problems.append("missing_canonical_account")
    if result.cleanup_status not in {"clean", "kept"}:
        problems.append(f"cleanup_not_complete:{result.cleanup_status}")
    if result.cleanup_status == "clean" and int(result.residual_rows) != 0:
        problems.append(f"cleanup_residual_rows:{result.residual_rows}")
    return problems


def _safe_finish_failure(
    *,
    run_id: str,
    user_id: int,
    payment_id: str,
    rows_touched: int,
    cleanup_status: str,
    exc: BaseException,
) -> None:
    error_code = f"probe_exception:{type(exc).__name__}"
    try:
        finish_probe_run(
            run_id=run_id,
            status="failed",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            error=error_code,
            evidence={
                "payment_id": payment_id,
                "user_id": int(user_id),
                "error_code": error_code,
            },
        )
    except (sqlite3.Error, RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError):
        # The original exception remains authoritative. Never replace it with a
        # secondary ledger-write failure or print connection details.
        return


def probe(
    *,
    package_id: str,
    user_id: int,
    source: str,
    apply: bool,
    cleanup: bool,
) -> LiveReconciliationProbeResult:
    uid = int(user_id)
    normalized_source = str(source or "").strip().lower()
    assert_synthetic_user_id(uid)
    if normalized_source not in ALLOWED_SOURCES:
        raise ValueError("probe source must be one of: telegram, vk, max")

    package = package_by_id(package_id)
    amount = f"{package.price_rub}.00"
    run_id = uuid.uuid4().hex
    payment_id = f"{PAYMENT_ID_PREFIX}-{package.package_id}-{run_id[:12]}"

    if not apply:
        return LiveReconciliationProbeResult(
            run_id=run_id,
            payment_id=payment_id,
            package_id=package.package_id,
            user_id=uid,
            amount=amount,
            applied=False,
            first_ok=True,
            first_inserted=False,
            first_problem="dry_run",
            cleanup_status="dry_run",
        )

    init_db()
    start_probe_run(
        probe_type=PROBE_TYPE,
        user_id=uid,
        run_id=run_id,
        evidence={
            "payment_id": payment_id,
            "package_id": package.package_id,
            "source": normalized_source,
            "apply": True,
        },
    )

    rows_touched = 0
    cleanup_status = "not_started"
    try:
        rows_touched += _cleanup_probe_rows(user_id=uid, payment_id=payment_id)
        before = _snapshot(user_id=uid, payment_id=payment_id)
        payload = _payload(
            payment_id=payment_id,
            user_id=uid,
            source=normalized_source,
            package_id=package.package_id,
            amount=amount,
        )
        first = record_yookassa_webhook(payload)
        second = record_yookassa_webhook(payload)
        after = _snapshot(user_id=uid, payment_id=payment_id)
        delta = _diff(before, after)
        rows_touched += _created_row_count(delta)

        residual_rows = sum(max(int(value), 0) for value in after.values())
        cleanup_status = "kept"
        if cleanup:
            rows_touched += _cleanup_probe_rows(user_id=uid, payment_id=payment_id)
            residual_rows = _residual_row_count(user_id=uid, payment_id=payment_id)
            cleanup_status = "clean" if residual_rows == 0 else "residual"

        result = LiveReconciliationProbeResult(
            run_id=run_id,
            payment_id=payment_id,
            package_id=package.package_id,
            user_id=uid,
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
            account_rows_delta=delta["accounts"],
            identity_rows_delta=delta["identities"],
            cleanup_status=cleanup_status,
            residual_rows=residual_rows,
            rows_touched=rows_touched,
        )
        problems = _validate_result(
            result=result,
            expected_tokens=int(package.tokens),
            expect_premium=package.package_id == DEFAULT_PACKAGE_ID,
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
    except (sqlite3.Error, RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        if cleanup:
            try:
                rows_touched += _cleanup_probe_rows(user_id=uid, payment_id=payment_id)
                residual = _residual_row_count(user_id=uid, payment_id=payment_id)
                cleanup_status = "clean" if residual == 0 else "residual"
            except (sqlite3.Error, RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError):
                cleanup_status = "cleanup_failed"
        _safe_finish_failure(
            run_id=run_id,
            user_id=uid,
            payment_id=payment_id,
            rows_touched=rows_touched,
            cleanup_status=cleanup_status,
            exc=exc,
        )
        raise


def _resolve_apply_mode(
    *,
    apply_webhooks: bool,
    allow_live_db_mutation: bool,
    keep_artifacts: bool,
) -> tuple[bool, str | None]:
    apply_requested = bool(apply_webhooks)
    mutation_authorized = bool(allow_live_db_mutation)
    if apply_requested != mutation_authorized:
        return False, "mutation_flags_must_be_used_together"
    if keep_artifacts and not (apply_requested and mutation_authorized):
        return False, "keep_artifacts_requires_authorized_mutation"
    return bool(apply_requested and mutation_authorized), None


def _result_ok(item: LiveReconciliationProbeResult) -> bool:
    if not item.applied:
        return (
            item.first_ok
            and item.first_inserted is False
            and item.first_problem == "dry_run"
            and item.cleanup_status == "dry_run"
        )
    return (
        item.first_ok
        and item.first_inserted
        and item.first_problem == ""
        and item.second_ok is True
        and item.second_inserted is False
        and item.second_problem == ""
        and item.cleanup_status in {"clean", "kept"}
        and (item.cleanup_status != "clean" or item.residual_rows == 0)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe local YooKassa reconciliation, token grants, premium side effects and duplicate idempotency"
    )
    parser.add_argument("--package", action="append", dest="packages", default=None)
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Reserved negative synthetic user id; a unique reserved id is generated when omitted",
    )
    parser.add_argument("--source", default="telegram", choices=sorted(ALLOWED_SOURCES))
    parser.add_argument("--apply-webhooks", action="store_true")
    parser.add_argument("--allow-live-db-mutation", action="store_true")
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    apply, mode_error = _resolve_apply_mode(
        apply_webhooks=bool(args.apply_webhooks),
        allow_live_db_mutation=bool(args.allow_live_db_mutation),
        keep_artifacts=bool(args.keep_artifacts),
    )
    if mode_error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "applied": False,
                    "database_touched": False,
                    "error_code": mode_error,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    packages = args.packages or [DEFAULT_PACKAGE_ID]
    user_id = int(args.user_id) if args.user_id is not None else _new_synthetic_user_id()
    try:
        results = [
            probe(
                package_id=package_id,
                user_id=user_id,
                source=str(args.source),
                apply=apply,
                cleanup=not bool(args.keep_artifacts),
            )
            for package_id in packages
        ]
    except (sqlite3.Error, RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "applied": apply,
                    "database_touched": apply,
                    "error_code": f"probe_failed:{type(exc).__name__}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    report = {
        "ok": all(_result_ok(item) for item in results),
        "mode": "apply" if apply else "dry_run",
        "applied": apply,
        "mutation_authorized": apply,
        "database_touched": apply,
        "results": [asdict(item) for item in results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
