from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.paths import DB_PATH  # noqa: E402
from services.storage_legacy_audit import storage_legacy_audit  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target_path(source: Path, timestamp: str) -> Path:
    return source.with_name(f"{source.name}.legacy-{timestamp}")


def _metadata_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".json")


def build_plan(*, apply: bool) -> dict:
    audit = storage_legacy_audit()
    source = Path(DB_PATH)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = _target_path(source, timestamp)
    metadata = _metadata_path(target)

    plan = {
        "ok": True,
        "applied": False,
        "action": "isolate_legacy_sqlite",
        "active_engine": audit.active_engine,
        "legacy_sqlite_present": audit.legacy_sqlite_present,
        "source": str(source),
        "target": str(target),
        "metadata": str(metadata),
        "hard_failures": audit.hard_failures,
        "reason": "dry_run" if not apply else "pending",
    }

    if audit.hard_failures:
        plan.update({"ok": False, "reason": "storage_audit_hard_failures"})
        return plan
    if audit.active_engine != "postgres":
        plan.update({"ok": False, "reason": "active_engine_not_postgres"})
        return plan
    if not audit.legacy_sqlite_present:
        plan.update({"reason": "already_absent"})
        return plan
    if not source.exists():
        plan.update({"ok": False, "reason": "source_missing"})
        return plan
    if target.exists() or metadata.exists():
        plan.update({"ok": False, "reason": "target_already_exists"})
        return plan

    stat = source.stat()
    plan.update(
        {
            "source_size_bytes": int(stat.st_size),
            "source_mtime_ns": int(stat.st_mtime_ns),
            "source_sha256": _sha256(source),
        }
    )

    if not apply:
        return plan

    metadata_payload = {
        **plan,
        "applied": True,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "operator_note": "Legacy SQLite file isolated by rename; no data was deleted.",
    }
    metadata.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(source, target)
    plan.update({"applied": True, "reason": "isolated", "legacy_sqlite_present_after": source.exists()})
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely isolate legacy SQLite artifact after Postgres migration")
    parser.add_argument("--apply", action="store_true", help="Rename the legacy SQLite file. Default is dry-run.")
    args = parser.parse_args()
    plan = build_plan(apply=bool(args.apply))
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return 0 if plan.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
