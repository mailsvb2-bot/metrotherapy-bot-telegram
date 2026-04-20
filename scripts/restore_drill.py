from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from scripts import backup_db, restore_db


def main() -> int:
    backup = restore_db._latest_backup()
    if backup is None or not backup.exists():
        backup_db.main()
        backup = restore_db._latest_backup()
    if backup is None or not backup.exists():
        raise SystemExit('No backup available for restore drill')

    restore_db._integrity_check(backup)
    with tempfile.TemporaryDirectory(prefix='metrotherapy_restore_drill_') as tmp:
        target = Path(tmp) / 'restore_check.db'
        restore_db._restore(backup, target)
        restore_db._integrity_check(target)
        conn = sqlite3.connect(target)
        try:
            conn.execute('SELECT 1').fetchone()
        finally:
            conn.close()
    print('OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
