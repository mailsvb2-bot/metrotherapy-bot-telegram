from __future__ import annotations

"""Live YooKassa duplicate-webhook idempotency probe.

This script intentionally posts the same synthetic successful YooKassa webhook
more than once and verifies that the second delivery does not create additional
practice grants, premium entitlements, delivery outbox rows or consultation
requests.

It requires --allow-live-db-mutation because the first webhook delivery writes
one synthetic payment into the configured application database.
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.practice_token_contract import PracticePackage, package_by_id  # noqa: E402


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] | None = None


def _read_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_value(name: str, dotenv: dict[str, str], default: str = "") -> str:
    return (os.getenv(name) or dotenv.get(name) or default).strip()


def _db_path(dotenv: dict[str, str]) -> Path:
    raw = (
        _env_value("DB_PATH", dotenv)
        or _env_value("METRO_DB_PATH", dotenv)
        or _env_value("SQLITE_DB_PATH", dotenv)
        or "data/data.db"
    )
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _webhook_secret(dotenv: dict[str, str]) -> str:
    return (
        _env_value("YOOKASSA_WEBHOOK_SECRET", dotenv)
        or _env_value("PAYMENT_WEBHOOK_SECRET", dotenv)
        or _env_value("WEBHOOK_SECRET", dotenv)
    )


def _payment_payload(*, payment_id: str, user_id: int, source: str, package: PracticePackage) -> dict[str, Any]:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": f"{int(package.price_rub)}.00", "currency": "RUB"},
            "metadata": {
                "project": "metrotherapy",
                "user_id": str(int(user_id)),
                "external_user_id": str(int(user_id)),
                "source": source,
                "kind": "tokens",
                "package_id": package.package_id,
            },
        },
    }


def _post_json(url: str, *, secret: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any], str]:
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Metrotherapy-Webhook-Secret"] = secret
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        status = int(exc.code)
    try:
        decoded = json.loads(body or "{}")
    except json.JSONDecodeError:
        decoded = {"raw": body[:300]}
    return status, decoded, body


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    return int(row[0])


def _wallet_available(conn: sqlite3.Connection, user_id: int) -> int:
    row = conn.execute("SELECT available_tokens FROM practice_wallets WHERE user_id=?", (int(user_id),)).fetchone()
    return int(row[0]) if row else 0


def _snapshot(db_path: Path, *, user_id: int, payment_id: str) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            "wallet_available": _wallet_available(conn, int(user_id)),
            "payments": _count(
                conn,
                "SELECT COUNT(*) FROM payments WHERE provider_charge_id=? OR telegram_charge_id=?",
                (payment_id, f"yookassa:{payment_id}"),
            ),
            "payment_token_grants": _count(
                conn,
                "SELECT COUNT(*) FROM payment_token_grants WHERE provider=? AND provider_payment_id=?",
                ("yookassa", payment_id),
            ),
            "grant_ledger": _count(
                conn,
                "SELECT COUNT(*) FROM practice_ledger WHERE provider=? AND provider_payment_id=? AND event_type='grant'",
                ("yookassa", payment_id),
            ),
            "premium_entitlements": _count(
                conn,
                "SELECT COUNT(*) FROM premium_entitlements WHERE provider=? AND provider_payment_id=?",
                ("yookassa", payment_id),
            ),
            "premium_outbox": _count(
                conn,
                "SELECT COUNT(*) FROM premium_delivery_outbox WHERE idempotency_key LIKE ?",
                (f"%{payment_id}%",),
            ),
            "consultation_requests": _count(
                conn,
                "SELECT COUNT(*) FROM consultation_requests WHERE provider=? AND provider_payment_id=?",
                ("yookassa", payment_id),
            ),
        }
    finally:
        conn.close()


def _expected_snapshot_delta(package: PracticePackage) -> dict[str, int]:
    expected = {
        "payments": 1,
        "payment_token_grants": 1,
        "grant_ledger": 1,
        "premium_entitlements": 0,
        "premium_outbox": 0,
        "consultation_requests": 0,
    }
    if package.package_id == "practice_antistress_60":
        expected["premium_entitlements"] = 1
        expected["premium_outbox"] = 1
    elif package.package_id == "practice_personal_month":
        expected["premium_entitlements"] = 2
        # Personal month creates two user-facing delivery notices:
        # video_course_access + consultation_user_notice.
        expected["premium_outbox"] = 2
        expected["consultation_requests"] = 1
    return expected


def _diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = sorted(set(before) | set(after))
    return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in keys}


def _webhook_result(name: str, status: int, decoded: dict[str, Any], *, expected_inserted: bool) -> ProbeResult:
    ok = status == 200 and decoded.get("ok") is True and decoded.get("problem") in {None, ""} and decoded.get("inserted") is expected_inserted
    return ProbeResult(
        name=name,
        ok=bool(ok),
        detail=f"status={status} ok={decoded.get('ok')} inserted={decoded.get('inserted')} problem={decoded.get('problem') or ''}",
        data=decoded,
    )


def _package_result(package: PracticePackage, *, before: dict[str, int], after_first: dict[str, int], after_second: dict[str, int]) -> list[ProbeResult]:
    first_delta = _diff(before, after_first)
    second_delta = _diff(after_first, after_second)
    expected = _expected_snapshot_delta(package)
    results: list[ProbeResult] = []

    for key, value in expected.items():
        results.append(
            ProbeResult(
                name=f"first_delta:{package.package_id}:{key}",
                ok=first_delta.get(key) == value,
                detail=f"actual={first_delta.get(key)} expected={value}",
                data={"before": before.get(key), "after_first": after_first.get(key)},
            )
        )

    results.append(
        ProbeResult(
            name=f"first_delta:{package.package_id}:wallet_available",
            ok=first_delta.get("wallet_available") == int(package.tokens),
            detail=f"actual={first_delta.get('wallet_available')} expected={package.tokens}",
            data={"before": before.get("wallet_available"), "after_first": after_first.get("wallet_available")},
        )
    )

    mutable_keys = [
        "wallet_available",
        "payments",
        "payment_token_grants",
        "grant_ledger",
        "premium_entitlements",
        "premium_outbox",
        "consultation_requests",
    ]
    for key in mutable_keys:
        results.append(
            ProbeResult(
                name=f"second_delta:{package.package_id}:{key}",
                ok=second_delta.get(key) == 0,
                detail=f"actual={second_delta.get(key)} expected=0",
                data={"after_first": after_first.get(key), "after_second": after_second.get(key)},
            )
        )
    return results


def _run_package(args: argparse.Namespace, *, secret: str, db_path: Path, package: PracticePackage, run_id: str) -> list[ProbeResult]:
    payment_id = f"idem-{run_id}-{package.package_id}"
    payload = _payment_payload(payment_id=payment_id, user_id=args.user_id, source=args.source, package=package)
    before = _snapshot(db_path, user_id=args.user_id, payment_id=payment_id)

    status1, decoded1, _body1 = _post_json(args.webhook_url, secret=secret, payload=payload)
    after_first = _snapshot(db_path, user_id=args.user_id, payment_id=payment_id)

    status2, decoded2, _body2 = _post_json(args.webhook_url, secret=secret, payload=payload)
    after_second = _snapshot(db_path, user_id=args.user_id, payment_id=payment_id)

    results = [
        _webhook_result(f"webhook_first:{package.package_id}", status1, decoded1, expected_inserted=True),
        _webhook_result(f"webhook_duplicate:{package.package_id}", status2, decoded2, expected_inserted=False),
    ]
    results.extend(_package_result(package, before=before, after_first=after_first, after_second=after_second))
    return results


def _selected_packages(package_ids: list[str]) -> tuple[PracticePackage, ...]:
    wanted = package_ids or ["practice_60", "practice_antistress_60", "practice_personal_month"]
    return tuple(package_by_id(package_id) for package_id in wanted)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook-url", default="http://127.0.0.1:8081/pay/yookassa/webhook")
    parser.add_argument("--user-id", type=int, default=990000002)
    parser.add_argument("--source", default="telegram")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--package", action="append", default=[])
    parser.add_argument("--allow-live-db-mutation", action="store_true")
    args = parser.parse_args()

    dotenv = _read_dotenv()
    db_path = _db_path(dotenv)
    secret = _webhook_secret(dotenv)
    run_id = args.run_id or uuid.uuid4().hex[:10]
    results: list[ProbeResult] = []

    if not args.allow_live_db_mutation:
        results.append(ProbeResult("safety", False, "This probe writes one synthetic payment per package; pass --allow-live-db-mutation"))
    if not secret:
        results.append(ProbeResult("webhook_secret", False, "No YOOKASSA_WEBHOOK_SECRET/PAYMENT_WEBHOOK_SECRET/WEBHOOK_SECRET found"))
    if not db_path.exists():
        results.append(ProbeResult("database", False, f"database_not_found:{db_path}"))

    if not results:
        for package in _selected_packages(args.package):
            results.extend(_run_package(args, secret=secret, db_path=db_path, package=package, run_id=run_id))

    report = {
        "ok": all(item.ok for item in results),
        "run_id": run_id,
        "user_id": int(args.user_id),
        "source": args.source,
        "db_path": str(db_path),
        "results": [asdict(item) for item in results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())