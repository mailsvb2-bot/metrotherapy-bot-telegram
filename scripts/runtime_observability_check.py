from __future__ import annotations

import json
import os
import re
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
    normalized = value.lower()

    # The old metrotherapy-bot.service was a duplicate runtime hazard when it
    # existed next to metrotherapy.service. If the legacy unit is absent, that is
    # also a safe state: there is no second process owner to disable or mask.
    if normalized in {"disabled", "masked", "not-found"}:
        return CheckResult(f"service:{service}:disabled", True, normalized)
    if code != 0 and ("not-found" in normalized or "no such file" in normalized):
        return CheckResult(f"service:{service}:disabled", True, "not-found")
    return CheckResult(f"service:{service}:disabled", False, value or f"code={code}")


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


def _nonzero_failed_counter(line: str) -> bool:
    """Return true only for failed counters that are actually non-zero.

    Runtime summaries often contain benign INFO lines such as
    "Premium delivery flush: sent=2 failed=0 skipped=0". Grepping for the word
    "failed" made observability fail even though the counter explicitly said 0.
    """
    lowered = line.lower()
    for match in re.finditer(r"\bfailed\s*[=:]\s*(\d+)\b", lowered):
        try:
            if int(match.group(1)) > 0:
                return True
        except ValueError:
            continue
    return False


def _journal_line_is_error(line: str) -> bool:
    lowered = line.lower()
    if _nonzero_failed_counter(line):
        return True
    if "failed=0" in lowered or "failed: 0" in lowered:
        lowered = lowered.replace("failed=0", "").replace("failed: 0", "")
    return any(
        token in lowered
        for token in (
            " | error | ",
            " | critical | ",
            "traceback",
            "exception",
            "address already in use",
            "telegramconflicterror",
            "application crashed",
            " unhandled ",
        )
    )


def _journal_errors(minutes: int, *, pid: str = "0") -> str:
    code, out = _run(
        ["journalctl", "-u", "metrotherapy.service", "--since", f"{minutes} minutes ago", "--no-pager", "-l"],
        timeout=20,
    )
    if code != 0:
        return out
    if not out.strip() or not pid or pid == "0":
        return "\n".join(line for line in out.splitlines() if _journal_line_is_error(line))

    current_pid_marker = f"python[{pid}]"
    return "\n".join(
        line for line in out.splitlines()
        if current_pid_marker in line and _journal_line_is_error(line)
    )


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _db_readiness_result(payload: dict[str, Any]) -> CheckResult:
    db_ready = bool(payload.get("db_ready"))
    db_engine = str(payload.get("db_engine") or "unknown").lower()
    require_postgres = _bool_env("METRO_OBS_REQUIRE_POSTGRES", False)

    if require_postgres and db_engine != "postgres":
        return CheckResult(
            "readiness:db_ready",
            False,
            f"db_ready={db_ready} db_engine={db_engine} require_postgres=1",
        )

    if db_engine == "sqlite":
        return CheckResult(
            "readiness:db_ready",
            db_ready,
            f"db_ready={db_ready} db_engine=sqlite warning=sqlite_non_prod",
        )

    return CheckResult("readiness:db_ready", db_ready, f"db_ready={db_ready} db_engine={db_engine}")


def collect_results() -> list[CheckResult]:
    health_url = os.getenv("METRO_OBS_HEALTH_URL", "http://127.0.0.1:8082/healthz")
    ready_url = os.getenv("METRO_OBS_READY_URL", "http://127.0.0.1:8082/readyz")
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
        results.append(CheckResult(
            "health:telegram_polling",
            payload.get("telegram_transport") == "polling" and not bool(payload.get("telegram_webhook_enabled")),
            f"transport={payload.get('telegram_transport')} webhook={payload.get('telegram_webhook_enabled')}",
        ))
        results.append(CheckResult(
            "health:messenger_webhook",
            bool(payload.get("messenger_webhook_enabled")),
            f"enabled={payload.get('messenger_webhook_enabled')}",
        ))

    ready_ok, ready_detail, ready_payload = _http_json(ready_url)
    results.append(CheckResult("http:ready", ready_ok and bool(ready_payload and ready_payload.get("ok")), ready_detail))
    if ready_payload:
        results.append(_db_readiness_result(ready_payload))
        results.append(CheckResult(
            "readiness:scheduler",
            bool(ready_payload.get("scheduler_ready")),
            f"scheduler_ready={ready_payload.get('scheduler_ready')}",
        ))
        results.append(CheckResult(
            "readiness:webhook",
            bool(ready_payload.get("webhook_ready")),
            f"webhook_ready={ready_payload.get('webhook_ready')}",
        ))

    ok, detail, payload = _http_json(webhook_health_url)
    results.append(CheckResult("http:webhook_health", ok and bool(payload and payload.get("ok")), detail))

    port_8081 = _port_owner(8081)
    port_8082 = _port_owner(8082)
    same_pid_ports = pid != "0" and f"pid={pid}" in port_8081 and f"pid={pid}" in port_8082
    results.append(CheckResult("ports:8081_8082_same_pid", same_pid_ports, f"pid={pid}"))

    journal = _journal_errors(int(os.getenv("METRO_OBS_JOURNAL_MINUTES", "20")), pid=pid)
    journal_detail = journal[:1000] if journal else f"none for pid={pid}"
    results.append(CheckResult("journal:no_recent_errors", journal.strip() == "", journal_detail))
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