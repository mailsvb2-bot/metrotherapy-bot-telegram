from __future__ import annotations
import logging


import sqlite3
import sys
from pathlib import Path


logger = logging.getLogger(__name__)
def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """Открыть SQLite в режиме read-only.

    Так скрипт не создаёт -wal/-shm и не требует прав записи.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def main() -> int:
    project_root = Path(__file__).resolve().parent
    db_path = project_root / "data.db"

    if not db_path.exists():
        logger.info(f"❌ База data.db не найдена: {db_path}")
        return 1

    conn = _open_readonly(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table'
            ORDER BY name;
            """
        )
        tables = [row[0] for row in cur.fetchall()]
        logger.info("📋 Таблицы в базе:")
        for t in tables:
            logger.info(" -", t)

        if "selected_plan" in tables:
            logger.info("\n✅ Таблица selected_plan СУЩЕСТВУЕТ")
        else:
            logger.info("\n❌ Таблица selected_plan ОТСУТСТВУЕТ")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())