from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from services.command_runner import run_command

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.disaster_recovery_status import disaster_recovery_status
from services.probe_ledger import get_recent_probe_runs
from services.release_control_report import format_release_control_report
from services.storage_legacy_audit import storage_legacy_audit

DEFAULT_EVIDENCE_DIR = Path(os.getenv("RELEASE_EVIDENCE_DIR", "/var/lib/metrotherapy/release_evidence"))


def _optional_bin(name: str, *, env_name: str | None = None) -> str | None:
    raw = (os.getenv(env_name or "") or name).strip()
    return shutil.which(raw) if raw else None


def _git(*args: str) -> str:
    git = _optional_bin("git", env_name="GIT_BIN")
    if not git:
        return "unknown"
    proc = run_command([git, *args], cwd=str(ROOT), text=True, capture_output=True, check=False)
    return (proc.stdout or "").strip() if proc.returncode == 0 else "unknown"


def _probe_run_to_dict(run) -> dict:
    return {
        "id": run.id,
        "probe_type": run.probe_type,
        "run_id": run.run_id,
        "user_id": run.user_id,
        "started_at_utc": run.started_at_utc,
        "finished_at_utc": run.finished_at_utc,
        "status": run.status,
        "cleanup_status": run.cleanup_status,
        "rows_touched": run.rows_touched,
        "error": run.error,
        "evidence": run.evidence,
    }


def build_snapshot() -> dict:
    storage = storage_legacy_audit()
    recovery = disaster_recovery_status(include_hash=False)
    recent_runs = get_recent_probe_runs(limit=25)
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git": {
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "commit": _git("rev-parse", "HEAD"),
            "commit_short": _git("rev-parse", "--short", "HEAD"),
        },
        "storage": storage.to_dict(),
        "disaster_recovery": recovery.to_dict(),
        "recent_probe_runs": [_probe_run_to_dict(run) for run in recent_runs],
        "admin_report_text": format_release_control_report(limit=25),
    }


def write_snapshot(*, evidence_dir: Path) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    snapshot = build_snapshot()
    commit_short = snapshot["git"].get("commit_short") or "unknown"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = evidence_dir / f"release-evidence-{stamp}-{commit_short}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Write immutable release evidence JSON snapshot")
    parser.add_argument("--dir", default=str(DEFAULT_EVIDENCE_DIR), help="Evidence directory")
    parser.add_argument("--print-only", action="store_true", help="Print JSON without writing a file")
    args = parser.parse_args()
    if args.print_only:
        print(json.dumps(build_snapshot(), ensure_ascii=False, sort_keys=True))
        return 0
    path = write_snapshot(evidence_dir=Path(args.dir))
    print(json.dumps({"ok": True, "evidence_path": str(path)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
