from __future__ import annotations

import argparse
import gzip
import os
import subprocess
import tempfile
from pathlib import Path


REQUIRED_TABLES = (
    "users",
    "payments",
    "jobs",
    "subscriptions",
    "deliveries",
)
DEFAULT_BACKUP_DIR = Path(os.getenv("METRO_POSTGRES_BACKUP_DIR", "/var/backups/metrotherapy/postgres"))
SUPPORTED_SUFFIXES = (".dump", ".sql", ".sql.gz")


def _target_url() -> str:
    value = os.getenv("METRO_RESTORE_DRILL_DATABASE_URL") or os.getenv("RESTORE_DATABASE_URL") or ""
    if not value.strip():
        raise SystemExit("METRO_RESTORE_DRILL_DATABASE_URL is required. Never point it to production.")
    if value == (os.getenv("DATABASE_URL") or ""):
        raise SystemExit("Restore drill target equals DATABASE_URL; refusing to touch production database")
    return value.strip()


def _is_supported_backup(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in SUPPORTED_SUFFIXES)


def latest_backup(*, backup_dir: Path = DEFAULT_BACKUP_DIR) -> Path:
    files = sorted(
        (path for path in backup_dir.iterdir() if path.is_file() and _is_supported_backup(path)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if backup_dir.exists() else []
    if not files:
        raise SystemExit(f"No Postgres backups found in {backup_dir}; expected one of: {', '.join(SUPPORTED_SUFFIXES)}")
    return files[0]


def _run(cmd: list[str], *, input_text: str | None = None) -> str:
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, input=input_text)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise SystemExit(out.strip() or proc.returncode)
    return out.strip()


def _reset_target_database(target: str) -> None:
    reset_sql = "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
    _run(["psql", target, "--set", "ON_ERROR_STOP=1", "--command", reset_sql])


def _restore_backup(*, dump_path: Path, target: str) -> None:
    lower_name = dump_path.name.lower()
    if lower_name.endswith(".dump"):
        _run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "--dbname", target, str(dump_path)])
        return
    if lower_name.endswith(".sql.gz"):
        with gzip.open(dump_path, "rt", encoding="utf-8") as fh:
            sql_text = fh.read()
        _reset_target_database(target)
        _run(["psql", target, "--set", "ON_ERROR_STOP=1"], input_text=sql_text)
        return
    if lower_name.endswith(".sql"):
        _reset_target_database(target)
        _run(["psql", target, "--set", "ON_ERROR_STOP=1", "--file", str(dump_path)])
        return
    raise SystemExit(f"Unsupported backup format: {dump_path}")


def restore_drill(*, dump_path: Path) -> None:
    if not dump_path.exists():
        raise SystemExit(f"Backup file not found: {dump_path}")
    target = _target_url()
    _restore_backup(dump_path=dump_path, target=target)
    table_sql = "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()"
    out = _run(["psql", target, "--tuples-only", "--no-align", "--command", table_sql])
    tables = {line.strip() for line in out.splitlines() if line.strip()}
    missing = [table for table in REQUIRED_TABLES if table not in tables]
    if missing:
        raise SystemExit("RESTORE_DRILL_FAILED missing tables: " + ", ".join(missing))
    print("POSTGRES_RESTORE_DRILL_OK tables=" + ",".join(sorted(tables)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a pg_dump backup into a non-production drill database and verify core tables")
    parser.add_argument("dump_path", nargs="?", help="Path to a .dump, .sql, or .sql.gz file. Use --latest to restore the newest backup.")
    parser.add_argument("--latest", action="store_true", help="Restore the newest backup from METRO_POSTGRES_BACKUP_DIR")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    args = parser.parse_args()
    dump = latest_backup(backup_dir=Path(args.backup_dir)) if args.latest else Path(args.dump_path or "")
    if not str(dump):
        raise SystemExit("dump_path is required unless --latest is used")
    restore_drill(dump_path=dump)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
