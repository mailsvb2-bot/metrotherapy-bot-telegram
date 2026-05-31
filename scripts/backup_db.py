from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_PATH = Path(__file__).resolve().parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

from core.paths import DB_PATH, ROOT
from services.db.runtime import is_postgres_enabled, redacted_db_target



def _backup_dir() -> Path:
    return ROOT / 'backups'



def _prune_old_backups(backup_dir: Path, keep: int) -> int:
    if keep <= 0:
        return 0
    backups = sorted(backup_dir.glob('data_*.db'))
    if len(backups) <= keep:
        return 0
    removed = 0
    for path in backups[: len(backups) - keep]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed



def main() -> int:
    if is_postgres_enabled():
        print(
            'SKIP: METRO_DB_ENGINE=postgres uses pg_dump backups, not SQLite backup_db.py. '
            f'Target={redacted_db_target()}'
        )
        return 0

    source = Path(DB_PATH)
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        print(f'SKIP: database not found: {source}')
        return 0

    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')
    target = backup_dir / f'data_{stamp}.db'

    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    keep = int(os.getenv('BACKUP_KEEP', '14') or 14)
    removed = _prune_old_backups(backup_dir, keep)

    print(target)
    if removed:
        print(f'PRUNED={removed}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
