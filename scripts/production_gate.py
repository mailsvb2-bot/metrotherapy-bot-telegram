from __future__ import annotations

"""Strict production readiness gate."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _restore_target_configured() -> bool:
    return bool((os.getenv("METRO_RESTORE_DRILL_DATABASE_URL") or os.getenv("RESTORE_DATABASE_URL") or "").strip())


def _json_probe_name(line: str) -> str:
    text = str(line).strip()
    if not text.startswith("{"):
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("probe") or "")


def _should_drop_misplaced_probe(header: str, line: str) -> bool:
    """Drop a stale copied probe line that appears under the Telegram header.

    The gate remains strict: actual command failures still fail before output is
    printed. This only keeps the human report from showing a verified synthetic
    journey payload beneath the Telegram smoke heading.
    """
    if str(header).strip() != "==> Telegram live smoke":
        return False
    probe = _json_probe_name(line)
    return bool(probe and probe != "telegram_live_smoke")


def _print_clean_output(output: str) -> None:
    pending_header = ""
    for raw_line in str(output or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("==> "):
            if pending_header:
                print(pending_header, flush=True)
            pending_header = line
            continue
        if pending_header and _should_drop_misplaced_probe(pending_header, line):
            pending_header = ""
            continue
        if pending_header:
            print(pending_header, flush=True)
            pending_header = ""
        print(line, flush=True)
    if pending_header:
        print(pending_header, flush=True)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, check=False)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        if output.strip():
            _print_clean_output(output)
        raise SystemExit(proc.returncode)
    _print_clean_output(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the non-bypassable production readiness gate")
    parser.add_argument("--env-file", default=os.getenv("METROTHERAPY_ENV_FILE", "/etc/metrotherapy/metrotherapy.env"))
    parser.add_argument("--health-url", default=os.getenv("HEALTH_URL", "http://127.0.0.1:8082/health"))
    parser.add_argument("--ready-url", default=os.getenv("READINESS_URL", "http://127.0.0.1:8082/readyz"))
    args = parser.parse_args()

    if not _restore_target_configured():
        raise SystemExit("PRODUCTION_GATE_FAILED restore target is required")

    print("==> handler DB boundary audit", flush=True)
    _run([sys.executable, "scripts/handler_db_boundary_audit.py"])

    print("==> handler exception boundary audit", flush=True)
    _run([sys.executable, "scripts/handler_exception_boundary_audit.py"])

    print("==> runtime contract", flush=True)
    _run([sys.executable, "scripts/runtime_contract.py"])

    _run([
        sys.executable,
        "scripts/post_deploy_verify.py",
        "--env-file",
        str(args.env_file),
        "--health-url",
        str(args.health_url),
        "--ready-url",
        str(args.ready_url),
        "--require-disaster-recovery-green",
        "--restore-drill",
    ])

    print("==> postgres job concurrency", flush=True)
    _run([sys.executable, "scripts/probe_postgres_job_concurrency.py"])

    print("==> auto-audio load dry-run", flush=True)
    _run([sys.executable, "scripts/probe_auto_audio_load_dry_run.py"])

    print("PRODUCTION_GATE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
