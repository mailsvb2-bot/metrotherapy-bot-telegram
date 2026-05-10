from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _run(cmd: list[str], *, timeout: float = 10) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _http_json(url: str, *, timeout: float = 5) -> tuple[bool, str, dict[str, Any] | None]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            status = getattr(response, "status", 0)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Ops boundary: a network probe must report degraded health instead of
        # crashing before the remaining checks can run.
        return False, f"{type(exc).__name__}: {exc}", None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    return status == 200, f"status={status}", payload


def _service_active(service: str) -> CheckResult:
    code, out = _run(["systemctl", "is-active", service])
    value = out.splitlines()[0].strip() if out else ""
    return CheckResult(f"service:{service}:active", code == 0 and value == "active", value or f"code={code}")


def _service_disabled(service: str) -> CheckResult:
    code, out = _run(["systemctl", "is-enabled", service])
    value = out.splitlines()[0].strip() if out else ""
    return CheckResult(f"service:{service}:disabled", value in {"disabled", "masked"}, value or f"code={code}")


def _main_pid(service: str) -> str:
    code, out = _run(["systemctl", "show", "-p", "MainPID", "--value", service])
    if code != 0:
        return "0"
    return out.splitlines()[0].strip() if out else "0"


def _rss_kb(pid: str) -> int:
    if not pid or pid == "0":
        return 0
    code, out = _run(["ps", "-o", "rss=", "-p", pid])
    if code != 0 or not out.strip():
        return 0
    try:
        return int(out.strip().split()[0])
    except (ValueError, IndexError):
        return 0


def _port_owner(port: int) -> str:
    code, out = _run(["sh", "-lc", f"ss -ltnp | grep ':{port}' || true"])
    return out


def _journal_errors(minutes: int) -> str:
    pattern = "error|exception|traceback|failed|critical|address already in use|conflict|Application crashed"
    code, out = _run([
        "sh",
        "-lc",
        f"journalctl -u metrotherapy.service --since '{minutes} minutes ago' --no-pager -l | grep -Ei '{pattern}' || true",
    ], timeout=20)
    return out


def collect_results() -> list[CheckResult]:
    health_url = os.getenv("METRO_OBS_HEALTH_URL", "http://127.0.0.1:8082/healthz")
    webhook_health_url = os.getenv("METRO_OBS_WEBHOOK_HEALTH_URL", "http://127.0.0.1:8081/healthz")
    service = os.getenv("METRO_OBS_SERVICE", "metrotherapy.service")
    duplicate_service = os.getenv("METRO_OBS_DUPLICATE_SERVICE", "metrotherapy-bot.service")
    max_rss_kb = int(os.getenv("METRO_OBS_MAX_RSS_KB", "450000"))

    results: list[CheckResult] = []
    results.append(_service_active(service))
    results.append(_service_disabled(duplicate_service))

    pid = _main_pid(service)
    rss = _rss_kb(pid)
    results.append(CheckResult("process:main_pid", bool(pid and pid != "0"), f"pid={pid}"))
    results.append(CheckResult("process:rss_limit", 0 < rss <= max_rss_kb, f"rss_kb={rss} max_rss_kb={max_rss_kb}"))

    ok, detail, payload = _http_json(health_url)
    results.append(CheckResult("http:health", ok and bool(payload and payload.get("ok")), detail))
    if payload:
        results.append(CheckResult("health:db_ready", bool(payload.get("db_ready")), f"db_engine={payload.get('db_engine')}"))
        results.append(CheckResult("health:telegram_polling", payload.get("telegram_transport") == "polling" and not bool(payload.get("telegram_webhook_enabled")), f"transport={payload.get('telegram_transport')} webhook={payload.get('telegram_webhook_enabled')}"))
        results.append(CheckResult("health:messenger_webhook", bool(payload.get("messenger_webhook_enabled")), f"enabled={payload.get('messenger_webhook_enabled')}"))

    ok, detail, payload = _http_json(webhook_health_url)
    results.append(CheckResult("http:webhook_health", ok and bool(payload and payload.get("ok")), detail))

    port_8081 = _port_owner(8081)
    port_8082 = _port_owner(8082)
    same_pid_ports = pid != "0" and f"pid={pid}" in port_8081 and f"pid={pid}" in port_8082
    results.append(CheckResult("ports:8081_8082_same_pid", same_pid_ports, f"pid={pid}"))

    journal = _journal_errors(int(os.getenv("METRO_OBS_JOURNAL_MINUTES", "20")))
    results.append(CheckResult("journal:no_recent_errors", journal.strip() == "", journal[:1000] if journal else "none"))
    return results


def main() -> int:
    results = collect_results()
    print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
    failed = [r for r in results if not r.ok]
    if failed:
        print("OBSERVABILITY CHECK: FAILED")
        for item in failed:
            print(f"ERROR: {item.name}: {item.detail}")
        return 2
    print("OBSERVABILITY CHECK: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
