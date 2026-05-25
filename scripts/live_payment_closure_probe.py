from __future__ import annotations

"""Live payment closure probe for the main-candidate branch.

The script has two explicit modes:

1. --check-checkout
   Calls the public YooKassa checkout route for every current public package and
   verifies that each route returns a redirect to YooKassa/YooMoney.

2. --apply-webhooks --allow-live-db-mutation
   Posts synthetic successful YooKassa webhooks to the local runtime endpoint and
   verifies that the live SQLite database reflects grants, premium entitlements
   and consultation requests.

The webhook mode intentionally requires an explicit mutation flag because it
writes test rows into the configured application database.
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.practice_token_contract import PracticePackage, public_practice_packages  # noqa: E402


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


def _http_request(*, url: str, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = int(getattr(response, "status", 0) or 0)
            returned_headers = {str(k): str(v) for k, v in response.headers.items()}
            text = response.read().decode("utf-8", "replace")
            return status, returned_headers, text
    except urllib.error.HTTPError as exc:
        returned_headers = {str(k): str(v) for k, v in exc.headers.items()} if exc.headers else {}
        text = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return int(exc.code), returned_headers, text


def _checkout_url(base_url: str, *, user_id: int, source: str, package: PracticePackage) -> str:
    query = urllib.parse.urlencode(
        {
            "source": source,
            "user_id": str(int(user_id)),
            "kind": "tokens",
            "package_id": package.package_id,
        }
    )
    return f"{base_url.rstrip('/')}/pay/yookassa?{query}"


def _check_checkout(base_url: str, *, user_id: int, source: str, package: PracticePackage) -> ProbeResult:
    url = _checkout_url(base_url, user_id=user_id, source=source, package=package)
    status, headers, body = _http_request(url=url)
    location = headers.get("Location") or headers.get("location") or ""
    ok = status in {301, 302, 303, 307, 308} and (
        "yoomoney.ru" in location or "yookassa.ru" in location or "checkout" in location
    )
    return ProbeResult(
        name=f"checkout:{package.package_id}",
        ok=ok,
        detail=f"status={status} location={location or '-'} body={body[:120]}",
        data={"url": url, "status": status, "location": location},
    )


def _payment_payload(*, run_id: str, user_id: int, source: str, package: PracticePackage) -> dict[str, Any]:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": f"closure-{run_id}-{package.package_id}",
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


def _post_webhook(webhook_url: str, *, secret: str, payload: dict[str, Any]) -> ProbeResult:
    payment_id = str(payload.get("object", {}).get("id") or "unknown")
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Metrotherapy-Webhook-Secret"] = secret
    status, _headers, body = _http_request(
        url=webhook_url,
        method="POST",
        body=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    ok = False
    detail = f"status={status} body={body[:300]}"
    try:
        decoded = json.loads(body or "{}")
        ok = status == 200 and bool(decoded.get("ok")) and not decoded.get("problem")
        detail = f"status={status} ok={decoded.get('ok')} problem={decoded.get('problem') or ''}"
    except json.JSONDecodeError:
        decoded = {"raw": body[:300]}
    return ProbeResult(name=f"webhook:{payment_id}", ok=ok, detail=detail, data=decoded)


def _fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def _verify_live_db(db_path: Path, *, user_id: int, package: PracticePackage, provider_payment_id: str) -> list[ProbeResult]:
    if not db_path.exists():
        return [ProbeResult(f"db:{package.package_id}", False, f"database_not_found:{db_path}")]

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        wallet = _fetch_one(conn, "SELECT available_tokens, reserved_tokens, used_tokens FROM practice_wallets WHERE user_id=?", (int(user_id),))
        grant = _fetch_one(
            conn,
            "SELECT tokens_granted, package_id FROM payment_token_grants WHERE provider=? AND provider_payment_id=?",
            ("yookassa", provider_payment_id),
        )
        entitlements = conn.execute(
            "SELECT entitlement_type FROM premium_entitlements WHERE provider=? AND provider_payment_id=? ORDER BY entitlement_type",
            ("yookassa", provider_payment_id),
        ).fetchall()
        outbox = conn.execute(
            "SELECT delivery_kind, status, last_error FROM premium_delivery_outbox WHERE user_id=? AND idempotency_key LIKE ? ORDER BY delivery_kind",
            (int(user_id), f"%{provider_payment_id}%"),
        ).fetchall()
        consultation = _fetch_one(
            conn,
            "SELECT package_id, status FROM consultation_requests WHERE provider=? AND provider_payment_id=?",
            ("yookassa", provider_payment_id),
        )
    finally:
        conn.close()

    results: list[ProbeResult] = []
    results.append(
        ProbeResult(
            name=f"db_grant:{package.package_id}",
            ok=bool(grant and int(grant["tokens_granted"]) == int(package.tokens) and str(grant["package_id"]) == package.package_id),
            detail=(f"tokens={grant['tokens_granted']} package_id={grant['package_id']}" if grant else "missing grant row"),
        )
    )
    results.append(
        ProbeResult(
            name=f"db_wallet:{package.package_id}",
            ok=bool(wallet and int(wallet["available_tokens"]) >= int(package.tokens)),
            detail=(
                f"available={wallet['available_tokens']} reserved={wallet['reserved_tokens']} used={wallet['used_tokens']}"
                if wallet
                else "missing wallet"
            ),
        )
    )

    entitlement_types = [str(row["entitlement_type"]) for row in entitlements]
    if package.package_id == "practice_antistress_60":
        results.append(
            ProbeResult(
                name="db_video_entitlement:practice_antistress_60",
                ok="stress_video_course" in entitlement_types,
                detail=f"entitlements={entitlement_types}",
            )
        )
        results.append(
            ProbeResult(
                name="db_video_outbox:practice_antistress_60",
                ok=any(str(row["delivery_kind"]) == "video_course_access" for row in outbox),
                detail=f"outbox={[dict(row) for row in outbox]}",
            )
        )
    if package.package_id == "practice_personal_month":
        results.append(
            ProbeResult(
                name="db_personal_entitlements:practice_personal_month",
                ok={"stress_video_course", "consultation_60m"}.issubset(set(entitlement_types)),
                detail=f"entitlements={entitlement_types}",
            )
        )
        results.append(
            ProbeResult(
                name="db_consultation_request:practice_personal_month",
                ok=bool(consultation and str(consultation["package_id"]) == package.package_id),
                detail=(f"package_id={consultation['package_id']} status={consultation['status']}" if consultation else "missing consultation request"),
            )
        )
    return results


def _selected_packages(names: list[str]) -> tuple[PracticePackage, ...]:
    packages = public_practice_packages()
    if not names or names == ["all"]:
        return packages
    by_id = {package.package_id: package for package in packages}
    missing = [name for name in names if name not in by_id]
    if missing:
        raise SystemExit(f"Unknown public package id(s): {', '.join(missing)}")
    return tuple(by_id[name] for name in names)


def run(args: argparse.Namespace) -> int:
    dotenv = _read_dotenv()
    packages = _selected_packages(args.package)
    run_id = args.run_id or uuid.uuid4().hex[:10]
    db_path = _db_path(dotenv)
    secret = _webhook_secret(dotenv)
    results: list[ProbeResult] = []

    if not args.check_checkout and not args.apply_webhooks:
        results.append(ProbeResult("mode", False, "Use --check-checkout and/or --apply-webhooks"))
    if args.apply_webhooks and not args.allow_live_db_mutation:
        results.append(ProbeResult("safety", False, "--apply-webhooks requires --allow-live-db-mutation"))
    if args.apply_webhooks and not secret:
        results.append(ProbeResult("webhook_secret", False, "No YOOKASSA_WEBHOOK_SECRET/PAYMENT_WEBHOOK_SECRET/WEBHOOK_SECRET found"))

    if results:
        report = {"ok": False, "run_id": run_id, "db_path": str(db_path), "results": [asdict(item) for item in results]}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    if args.check_checkout:
        for package in packages:
            results.append(_check_checkout(args.base_url, user_id=args.user_id, source=args.source, package=package))

    if args.apply_webhooks:
        for package in packages:
            payload = _payment_payload(run_id=run_id, user_id=args.user_id, source=args.source, package=package)
            provider_payment_id = str(payload["object"]["id"])
            webhook_result = _post_webhook(args.webhook_url, secret=secret, payload=payload)
            results.append(webhook_result)
            if webhook_result.ok:
                results.extend(_verify_live_db(db_path, user_id=args.user_id, package=package, provider_payment_id=provider_payment_id))

    report = {
        "ok": all(item.ok for item in results),
        "run_id": run_id,
        "user_id": int(args.user_id),
        "source": args.source,
        "db_path": str(db_path),
        "packages": [package.package_id for package in packages],
        "results": [asdict(item) for item in results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://metrotherapy-bot.metrotherapy.ru")
    parser.add_argument("--webhook-url", default="http://127.0.0.1:8081/pay/yookassa/webhook")
    parser.add_argument("--user-id", type=int, default=990000001)
    parser.add_argument("--source", default="acceptance")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--package", action="append", default=[], help="Public package id to test; repeat or use all")
    parser.add_argument("--check-checkout", action="store_true")
    parser.add_argument("--apply-webhooks", action="store_true")
    parser.add_argument("--allow-live-db-mutation", action="store_true")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
