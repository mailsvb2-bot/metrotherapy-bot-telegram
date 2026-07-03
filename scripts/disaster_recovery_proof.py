from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.disaster_recovery_status import disaster_recovery_status
from scripts.postgres_restore_drill import latest_backup


def _run_restore_drill() -> str:
    dump = latest_backup()
    proc = subprocess.run(
        [sys.executable, "scripts/postgres_restore_drill.py", str(dump)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        raise SystemExit(output or f"restore drill failed with exit={proc.returncode}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Check backup readiness and optionally run Postgres restore drill")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--strict", action="store_true", help="Fail when no backup exists")
    parser.add_argument("--restore-drill", action="store_true", help="Run restore drill into configured non-production database")
    parser.add_argument("--hash", action="store_true", help="Include sha256 for latest backup")
    args = parser.parse_args()

    status = disaster_recovery_status(include_hash=bool(args.hash))
    restore_output = None
    if args.restore_drill:
        restore_output = _run_restore_drill()

    payload = {"ok": status.ok, "probe": "disaster_recovery_proof", "status": status.to_dict(), "restore_output": restore_output}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict and not status.ok:
        return 2
    return 0 if status.ok or not args.strict else 2


if __name__ == "__main__":
    raise SystemExit(main())
