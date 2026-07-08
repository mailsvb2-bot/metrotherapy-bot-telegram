from __future__ import annotations

"""Production acceptance gate runner.

This script composes the existing canonical checks instead of creating a second
validator brain. It is designed for a production host after git pull/restart and
before traffic or ad spend is increased.

By default it does not run full pytest on the live host. Use --include-pytest
only in an approved maintenance window.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AcceptanceResult:
    name: str
    ok: bool
    detail: str


def _merged_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("APP_ENV", "prod")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if extra:
        env.update(extra)
    return env


def _run(name: str, cmd: list[str], *, timeout: int = 120, extra_env: dict[str, str] | None = None) -> AcceptanceResult:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_merged_env(extra_env),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AcceptanceResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}")

    output = (proc.stdout + proc.stderr).strip()
    tail_lines = 20 if proc.returncode == 0 else 60
    tail = "\n".join(output.splitlines()[-tail_lines:]) if output else f"exit={proc.returncode}"
    return AcceptanceResult(name=name, ok=proc.returncode == 0, detail=tail)


def _http_json(name: str, url: str, *, readiness: bool = False, require_telegram_polling: bool = False) -> AcceptanceResult:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            status = int(getattr(response, "status", 0) or 0)
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return AcceptanceResult(name=name, ok=False, detail=f"status={exc.code} body={raw[:500]}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return AcceptanceResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}")

    if status != 200:
        return AcceptanceResult(name=name, ok=False, detail=f"status={status} body={raw[:500]}")
    try:
        payload: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        return AcceptanceResult(name=name, ok=False, detail=f"bad_json:{exc} body={raw[:500]}")
    if payload.get("ok") is not True:
        return AcceptanceResult(name=name, ok=False, detail=f"payload_ok_false:{payload}")
    if readiness:
        required_true = ["db_ready", "schema_ready", "scheduler_ready", "webhook_ready"]
        missing = [key for key in required_true if payload.get(key) is not True]
        if missing:
            return AcceptanceResult(name=name, ok=False, detail=f"missing_true={missing} payload={payload}")
    if require_telegram_polling:
        telegram_transport = str(payload.get("telegram_transport") or "").strip().lower()
        telegram_webhook_enabled = payload.get("telegram_webhook_enabled")
        if telegram_transport != "polling" or telegram_webhook_enabled is True:
            return AcceptanceResult(
                name=name,
                ok=False,
                detail=f"telegram_polling_contract_failed transport={telegram_transport} webhook_enabled={telegram_webhook_enabled}",
            )
    return AcceptanceResult(
        name=name,
        ok=True,
        detail=(
            f"status={status} probe={payload.get('probe')} db_engine={payload.get('db_engine')} "
            f"telegram={payload.get('telegram_transport')} messenger_webhook={payload.get('messenger_webhook_enabled')}"
        ),
    )


def _method_probe(name: str, url: str, *, expected_status: int = 405, expected_allow: str = "POST") -> AcceptanceResult:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            status = int(getattr(response, "status", 0) or 0)
            allow = response.headers.get("Allow", "")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        allow = exc.headers.get("Allow", "") if exc.headers else ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return AcceptanceResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}")
    ok = status == expected_status and expected_allow.upper() in allow.upper()
    return AcceptanceResult(name=name, ok=ok, detail=f"status={status} allow={allow}")


def collect_results(*, include_pytest: bool = False) -> list[AcceptanceResult]:
    public_base = os.getenv("METRO_PUBLIC_BOT_BASE_URL", os.getenv("MESSENGER_PUBLIC_BASE_URL", "https://metrotherapy-bot.metrotherapy.ru")).rstrip("/")
    results: list[AcceptanceResult] = []
    results.append(_run("compileall:project", [sys.executable, "-m", "compileall", "-q", "app.py", "main.py", "config", "core", "handlers", "interfaces", "keyboards", "runtime", "scripts", "services", "tests", "tools"], timeout=180))
    if include_pytest:
        results.append(_run("pytest", [sys.executable, "-m", "pytest", "-q"], timeout=300))
    else:
        results.append(AcceptanceResult("pytest", True, "skipped by default on production host; use --include-pytest only in an approved maintenance window"))
    results.append(_run("prod_readiness", [sys.executable, "scripts/prod_readiness_check.py"], timeout=120))
    results.append(_run("runtime_observability", [sys.executable, "scripts/runtime_observability_check.py"], timeout=60))
    results.append(_run("user_scenario_gate:prod", [sys.executable, "scripts/user_scenario_gate.py", "--mode", "prod"], timeout=120))
    results.append(_http_json("http:local_health", "http://127.0.0.1:8082/healthz", require_telegram_polling=True))
    results.append(_http_json("http:local_ready", "http://127.0.0.1:8082/readyz", readiness=True))
    results.append(_http_json("http:local_webhook_health", "http://127.0.0.1:8081/healthz"))
    if public_base:
        results.append(_method_probe("http:vk_webhook_get_rejected", f"{public_base}/webhooks/vk"))
        results.append(_method_probe("http:max_webhook_get_rejected", f"{public_base}/webhooks/max"))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run production acceptance checks without full pytest by default")
    parser.add_argument("--include-pytest", action="store_true", help="Run full pytest; use only during an approved maintenance window")
    args = parser.parse_args()

    results = collect_results(include_pytest=bool(args.include_pytest))
    print(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2))
    failed = [item for item in results if not item.ok]
    if failed:
        print("PRODUCTION ACCEPTANCE: FAILED")
        for item in failed:
            print(f"ERROR: {item.name}: {item.detail}")
        return 2
    print("PRODUCTION ACCEPTANCE: OK")
    print("Manual live-flow stop-conditions still required: Telegram demo, VK message, MAX message, payment test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
