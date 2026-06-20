from __future__ import annotations

"""Strict production readiness gate.

This wrapper intentionally has no skip flags. It is the stop-condition command for
calling a deployment production-ready:

- production runtime contract, including Telegram polling-only transport;
- full pytest through post_deploy_verify.py;
- strict validator + smoke;
- storage ambiguity audit;
- disaster recovery GREEN status;
- real Postgres restore drill against a non-production restore target;
- scheduler, native Postgres concurrency, auto-audio, payment reconciliation and synthetic journey probes;
- live Telegram Bot API smoke;
- local health/readiness probes.

It does not send Telegram messages. It requires a safe restore target via
METRO_RESTORE_DRILL_DATABASE_URL or RESTORE_DATABASE_URL.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _restore_target_configured() -> bool:
    return bool((os.getenv("METRO_RESTORE_DRILL_DATABASE_URL") or os.getenv("RESTORE_DATABASE_URL") or "").strip())


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the non-bypassable production readiness gate")
    parser.add_argument("--env-file", default=os.getenv("METROTHERAPY_ENV_FILE", "/etc/metrotherapy/metrotherapy.env"))
    parser.add_argument("--health-url", default=os.getenv("HEALTH_URL", "http://127.0.0.1:8082/health"))
    parser.add_argument("--ready-url", default=os.getenv("READINESS_URL", "http://127.0.0.1:8082/readyz"))
    args = parser.parse_args()

    if not _restore_target_configured():
        raise SystemExit(
            "PRODUCTION_GATE_FAILED restore target is required: "
            "set METRO_RESTORE_DRILL_DATABASE_URL or RESTORE_DATABASE_URL to a safe non-production database"
        )

    print("==> runtime contract")
    _run([sys.executable, "scripts/runtime_contract.py"])

    cmd = [
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
    ]
    _run(cmd)

    print("==> postgres job concurrency")
    _run([sys.executable, "scripts/probe_postgres_job_concurrency.py"])

    print("PRODUCTION_GATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
