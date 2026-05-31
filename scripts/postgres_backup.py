from __future__ import annotations

import argparse
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_BACKUP_DIR = Path(os.getenv("METRO_POSTGRES_BACKUP_DIR", "/var/backups/metrotherapy/postgres"))
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _database_url() -> str:
    engine = (os.getenv("METRO_DB_ENGINE") or "").strip().lower()
    if engine != "postgres":
        raise SystemExit("METRO_DB_ENGINE=postgres is required for Postgres backup")
    value = os.getenv("DATABASE_URL") or os.getenv("METRO_DATABASE_URL") or ""
    if not value.strip():
        raise SystemExit("DATABASE_URL is required for Postgres backup")
    return value.strip()


def _db_name(url: str) -> str:
    parsed = urlparse(url)
    name = (parsed.path or "").strip("/") or "metrotherapy"
    safe = "".join(ch for ch in name if ch.isalnum() or ch in {"_", "-"})
    return safe or "metrotherapy"


def _safe_backup_dir(backup_dir: Path) -> Path:
    resolved = backup_dir.expanduser().resolve()
    root = PROJECT_ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        pass
    else:
        raise SystemExit("Postgres backup dir must not be inside the repository")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def create_backup(*, backup_dir: Path = DEFAULT_BACKUP_DIR) -> Path:
    url = _database_url()
    backup_dir = _safe_backup_dir(backup_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = backup_dir / f"{_db_name(url)}_{stamp}.dump"
    proc = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--exclude-table=public.*manual_backup*",
            "--exclude-table=public.*_backup_*",
            "--file",
            str(out),
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        detail = (proc.stderr or proc.stdout or "").strip() or str(proc.returncode)
        raise SystemExit("POSTGRES_BACKUP_FAILED " + detail)
    if not out.exists() or out.stat().st_size <= 0:
        raise SystemExit("POSTGRES_BACKUP_FAILED backup file was not created or is empty")
    print(f"POSTGRES_BACKUP_OK path={out} bytes={out.stat().st_size}")
    return out


def prune_backups(*, backup_dir: Path = DEFAULT_BACKUP_DIR, keep: int = 14) -> None:
    backup_dir = backup_dir.expanduser().resolve()
    if keep <= 0 or not backup_dir.exists():
        return
    files = sorted(backup_dir.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    for item in files[keep:]:
        item.unlink(missing_ok=True)
        print(f"POSTGRES_BACKUP_PRUNED {item}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a pg_dump backup for Metrotherapy Postgres")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument("--keep", type=int, default=int(os.getenv("METRO_POSTGRES_BACKUP_KEEP", "14")))
    args = parser.parse_args()
    create_backup(backup_dir=Path(args.backup_dir))
    prune_backups(backup_dir=Path(args.backup_dir), keep=args.keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
