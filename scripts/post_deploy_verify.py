from __future__ import annotations

"""Post-deploy verification bundle for the Metrotherapy service.

This script is intentionally explicit and conservative. It combines the checks
that were previously run manually after deploy into one repeatable command:

- optional pytest run;
- production validator;
- smoke bootstrap;
- storage/legacy SQLite ambiguity audit;
- DB-backed scheduler/idempotency probe;
- auto-audio dry-run probe without Telegram sends;
- local payment reconciliation / entitlement / idempotency probe;
- optional Postgres restore drill;
- local health/readiness HTTP probes.

It does not modify systemd units, does not contact YooKassa, does not delete
legacy SQLite files, and does not send Telegram messages.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = Path("/etc/metrotherapy/metrotherapy.env")


def _load_env_file(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    env_path = Path(path)
    if not env_path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        try:
            parts = shlex.split(value, posix=True)
            loaded[key] = parts[0] if len(parts) == 1 else value
        except ValueError:
            loaded[key] = value.strip('"').strip("'")
    return loaded


def _run(cmd: list[str], *, env: Mapping[str, str] | None = None) -> str:
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})
    proc = subprocess.run(cmd, cwd=str(ROOT), env=merged_env, text=True, capture_output=True, check=False)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise SystemExit(
            "POST_DEPLOY_VERIFY_FAILED command="
            + " ".join(cmd)
            + f" exit={proc.returncode}\n"
            + output.strip()
        )
    return output.strip()


def _decode_error_body(exc: urllib.error.HTTPError) -> str:
    return exc.read().decode("utf-8", errors="replace")


def _truncate(value: str, *, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _parse_json_body(*, url: str, body: str) -> dict:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} invalid_json={_truncate(body, limit=300)}") from exc


def _parse_command_json(*, command_name: str, output: str) -> dict:
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"POST_DEPLOY_VERIFY_FAILED command={command_name} invalid_json={_truncate(output, limit=500)}"
        ) from exc


def _with_path(url: str, path: str) -> str:
    parts = urlsplit(str(url))
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _alias_urls(url: str, *, aliases: tuple[str, ...]) -> list[str]:
    urls = [str(url)]
    for alias in aliases:
        candidate = _with_path(str(url), alias)
        if candidate not in urls:
            urls.append(candidate)
    return urls


def _http_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # nosec B310 - local operator-provided probe URL
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = _decode_error_body(exc)
        payload = _parse_json_body(url=url, body=body) if body.strip().startswith("{") else None
        if payload is not None:
            raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} status={exc.code} payload={payload}") from exc
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} status={exc.code} body={_truncate(body)}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} err={exc}") from exc
    payload = _parse_json_body(url=url, body=body)
    if payload.get("ok") is not True:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} payload={payload}")
    return payload


def _http_json_any(urls: list[str]) -> tuple[dict, str]:
    errors: list[str] = []
    for url in urls:
        try:
            return _http_json(url), url
        except SystemExit as exc:
            errors.append(str(exc))
    raise SystemExit("POST_DEPLOY_VERIFY_FAILED all_probe_urls_failed\n" + "\n".join(errors))


def _verify_payment_probe(payload: dict) -> dict:
    if payload.get("ok") is not True or payload.get("applied") is not True:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED payment_probe payload={payload}")
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED payment_probe missing_results payload={payload}")
    first = results[0]
    if not isinstance(first, dict):
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED payment_probe malformed_result payload={payload}")
    checks = {
        "first_ok": first.get("first_ok") is True,
        "first_inserted": first.get("first_inserted") is True,
        "first_problem_empty": first.get("first_problem") == "",
        "second_ok": first.get("second_ok") is True,
        "second_inserted_false": first.get("second_inserted") is False,
        "second_problem_empty": first.get("second_problem") == "",
        "wallet_delta_positive": int(first.get("wallet_delta") or 0) > 0,
        "grant_rows_one": int(first.get("grant_rows_delta") or 0) == 1,
        "payment_rows_one": int(first.get("payment_rows_delta") or 0) == 1,
        "entitlement_rows_positive": int(first.get("entitlement_rows_delta") or 0) > 0,
        "outbox_rows_positive": int(first.get("outbox_rows_delta") or 0) > 0,
        "consultation_rows_positive": int(first.get("consultation_rows_delta") or 0) > 0,
        "cleanup_clean": first.get("cleanup_status") == "clean",
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED payment_probe failed_checks={failed} result={first}")
    return {
        "ok": True,
        "probe": "payment_entitlement",
        "payment_id": first.get("payment_id"),
        "package_id": first.get("package_id"),
        "wallet_delta": first.get("wallet_delta"),
        "entitlement_rows_delta": first.get("entitlement_rows_delta"),
        "outbox_rows_delta": first.get("outbox_rows_delta"),
        "consultation_rows_delta": first.get("consultation_rows_delta"),
        "cleanup_status": first.get("cleanup_status"),
        "rows_touched": first.get("rows_touched"),
    }


def _verify_storage_audit(payload: dict) -> dict:
    if payload.get("ok") is not True:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED storage_audit payload={payload}")
    if payload.get("active_engine") != "postgres":
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED storage_audit active_engine={payload.get('active_engine')}")
    if payload.get("repo_local_sqlite_present") is True:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED storage_audit repo_local_sqlite_present payload={payload}")
    if payload.get("disallowed_direct_sqlite_connects"):
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED storage_audit disallowed_direct_sqlite_connects payload={payload}")
    return {
        "ok": True,
        "probe": "storage_legacy_audit",
        "status": payload.get("status"),
        "active_engine": payload.get("active_engine"),
        "legacy_sqlite_present": payload.get("legacy_sqlite_present"),
        "repo_local_sqlite_present": payload.get("repo_local_sqlite_present"),
        "direct_sqlite_connects": len(payload.get("direct_sqlite_connects") or []),
        "disallowed_direct_sqlite_connects": len(payload.get("disallowed_direct_sqlite_connects") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeatable post-deploy proof checks")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip pytest for faster repeated local checks")
    parser.add_argument("--skip-payment-probe", action="store_true", help="Skip the local payment entitlement proof probe")
    parser.add_argument("--skip-storage-audit", action="store_true", help="Skip the storage/legacy SQLite ambiguity audit")
    parser.add_argument("--restore-drill", action="store_true", help="Run postgres_restore_drill.py --latest as part of the bundle")
    parser.add_argument("--env-file", default=os.getenv("METROTHERAPY_ENV_FILE", str(DEFAULT_ENV_FILE)))
    parser.add_argument("--health-url", default=os.getenv("HEALTH_URL", "http://127.0.0.1:8082/health"))
    parser.add_argument("--ready-url", default=os.getenv("READINESS_URL", "http://127.0.0.1:8082/readyz"))
    args = parser.parse_args()

    service_env = _load_env_file(args.env_file)
    if service_env:
        print(f"==> loaded env file: {args.env_file} ({len(service_env)} keys)", flush=True)
    else:
        print(f"==> env file not loaded or empty: {args.env_file}", flush=True)

    if not args.skip_pytest:
        print("==> pytest", flush=True)
        print(_run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], env=service_env))

    strict_env = {
        **service_env,
        "APP_ENV": "prod",
        "VALIDATOR_RELEASE_MODE": "1",
        "VALIDATOR_GUARDRAILS_STRICT": "1",
    }

    print("==> prod validator", flush=True)
    print(_run([sys.executable, "scripts/validate_project.py"], env=strict_env))

    print("==> smoke", flush=True)
    print(_run([sys.executable, "scripts/smoke.py"], env=strict_env))

    if not args.skip_storage_audit:
        print("==> storage legacy audit", flush=True)
        storage_output = _run([sys.executable, "scripts/storage_legacy_audit.py", "--json", "--strict"], env=service_env)
        print(json.dumps(_verify_storage_audit(_parse_command_json(command_name="storage legacy audit", output=storage_output)), ensure_ascii=False))

    print("==> scheduler job probe", flush=True)
    print(_run([sys.executable, "scripts/probe_scheduler_job_live.py"], env=service_env))

    print("==> auto-audio dry-run probe", flush=True)
    print(_run([sys.executable, "scripts/probe_auto_audio_dry_run.py"], env=service_env))

    if not args.skip_payment_probe:
        print("==> payment entitlement probe", flush=True)
        payment_output = _run(
            [
                sys.executable,
                "scripts/probe_payment_reconciliation_live.py",
                "--apply-webhooks",
                "--allow-live-db-mutation",
            ],
            env=service_env,
        )
        print(json.dumps(_verify_payment_probe(_parse_command_json(command_name="payment entitlement probe", output=payment_output)), ensure_ascii=False))

    if args.restore_drill:
        print("==> postgres restore drill", flush=True)
        print(_run([sys.executable, "scripts/postgres_restore_drill.py", "--latest"], env=service_env))

    print("==> health", flush=True)
    health, health_url = _http_json_any(_alias_urls(str(args.health_url), aliases=("/health", "/healthz")))
    print(
        json.dumps(
            {"ok": health.get("ok"), "probe": health.get("probe"), "db_engine": health.get("db_engine"), "url": health_url},
            ensure_ascii=False,
        )
    )

    print("==> ready", flush=True)
    ready, ready_url = _http_json_any(_alias_urls(str(args.ready_url), aliases=("/readyz", "/ready")))
    print(
        json.dumps(
            {
                "ok": ready.get("ok"),
                "probe": ready.get("probe"),
                "db_ready": ready.get("db_ready"),
                "schema_ready": ready.get("schema_ready"),
                "scheduler_ready": ready.get("scheduler_ready"),
                "webhook_ready": ready.get("webhook_ready"),
                "url": ready_url,
            },
            ensure_ascii=False,
        )
    )

    print("POST_DEPLOY_VERIFY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
