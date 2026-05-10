from __future__ import annotations

import argparse
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_BACKUP_DIR = Path(os.getenv("METRO_POSTGRES_BACKUP_DIR", "/var/backups/metrotherapy/postgres"))


def _database_url() -> str:
    value = os.getenv("DATABASE_URL") or os.getenv("METRO_DATABASE_URL") or ""
    if not value.strip():
        raise SystemExit("DATABASE_URL is required for Postgres backup")
    return value.strip()


def _db_name(url: str) -> str:
    parsed = urlparse(url)
    name = (parsed.path or "").strip("/") or "metrotherapy"
    safe = "".join(ch for ch in name if ch.isalnum() or ch in {"_", "-"})
    return safe or "metrotherapy"


def create_backup(*, backup_dir: Path = DEFAULT_BACKUP_DIR) -> Path:
    url = _database_url()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = backup_dir / f"{_db_name(url)}_{stamp}.dump"
    proc = subprocess.run(
        ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(out), url],
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    print(f"POSTGRES_BACKUP_OK {out}")
    return out


def prune_backups(*, backup_dir: Path = DEFAULT_BACKUP_DIR, keep: int = 14) -> None:
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
