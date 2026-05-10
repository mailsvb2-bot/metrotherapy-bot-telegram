from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


REQUIRED_TABLES = (
    "users",
    "payments",
    "jobs",
    "subscriptions",
    "deliveries",
)


def _target_url() -> str:
    value = os.getenv("METRO_RESTORE_DRILL_DATABASE_URL") or os.getenv("RESTORE_DATABASE_URL") or ""
    if not value.strip():
        raise SystemExit("METRO_RESTORE_DRILL_DATABASE_URL is required. Never point it to production.")
    if value == (os.getenv("DATABASE_URL") or ""):
        raise SystemExit("Restore drill target equals DATABASE_URL; refusing to touch production database")
    return value.strip()


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise SystemExit(out.strip() or proc.returncode)
    return out.strip()


def restore_drill(*, dump_path: Path) -> None:
    if not dump_path.exists():
        raise SystemExit(f"Backup file not found: {dump_path}")
    target = _target_url()
    _run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "--dbname", target, str(dump_path)])
    table_sql = "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()"
    out = _run(["psql", target, "--tuples-only", "--no-align", "--command", table_sql])
    tables = {line.strip() for line in out.splitlines() if line.strip()}
    missing = [table for table in REQUIRED_TABLES if table not in tables]
    if missing:
        raise SystemExit("RESTORE_DRILL_FAILED missing tables: " + ", ".join(missing))
    print("POSTGRES_RESTORE_DRILL_OK tables=" + ",".join(sorted(tables)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a pg_dump into a non-production drill database and verify core tables")
    parser.add_argument("dump_path")
    args = parser.parse_args()
    restore_drill(dump_path=Path(args.dump_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
