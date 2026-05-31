from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.paths import DB_PATH, ROOT
from services.db.runtime import is_postgres_enabled, redacted_db_target



def _backup_dir() -> Path:
    return ROOT / 'backups'



def _latest_backup() -> Path | None:
    backups = sorted(_backup_dir().glob('data_*.db'))
    return backups[-1] if backups else None



def _integrity_check(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute('PRAGMA integrity_check').fetchone()
        if not row or str(row[0]).lower() != 'ok':
            raise SystemExit(f'Integrity check failed for {path}: {row}')
    finally:
        conn.close()



def _restore(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        safety = _backup_dir() / f'pre_restore_{datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")}.db'
        safety.parent.mkdir(parents=True, exist_ok=True)
        src_existing = sqlite3.connect(target)
        dst_safety = sqlite3.connect(safety)
        try:
            src_existing.backup(dst_safety)
        finally:
            dst_safety.close()
            src_existing.close()

    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()



def main(argv: list[str] | None = None) -> int:
    if is_postgres_enabled():
        raise SystemExit(
            'REFUSE: METRO_DB_ENGINE=postgres uses pg_dump/psql restore, not SQLite restore_db.py. '
            f'Target={redacted_db_target()}'
        )

    parser = argparse.ArgumentParser(description='Restore Metrotherapy SQLite DB from backup')
    parser.add_argument('--from-path', dest='from_path', default='', help='Path to backup .db file')
    parser.add_argument('--verify', action='store_true', help='Run PRAGMA integrity_check after restore')
    args = parser.parse_args(argv)

    source = Path(args.from_path).expanduser() if args.from_path else _latest_backup()
    if source is None or not source.exists():
        raise SystemExit('No backup file found to restore')

    _integrity_check(source)
    _restore(source, Path(DB_PATH))
    if args.verify:
        _integrity_check(Path(DB_PATH))
    print(DB_PATH)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
