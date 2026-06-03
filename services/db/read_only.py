from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from services.db.core import get_connection
from services.db.runtime import is_postgres_enabled

log = logging.getLogger(__name__)

_WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "TRUNCATE", "VACUUM")


def _is_write_sql(sql: str) -> bool:
    statement = (sql or "").lstrip().upper()
    return statement.startswith(_WRITE_PREFIXES)


class ReadOnlyCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor
        self.rowcount = getattr(cursor, "rowcount", -1)

    def execute(self, sql: str, params: Sequence[Any] = ()): 
        if _is_write_sql(sql):
            raise RuntimeError("read-only DB context rejected write SQL")
        result = self._cursor.execute(sql, params)
        self.rowcount = getattr(self._cursor, "rowcount", -1)
        return self if result is self._cursor else result

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class ReadOnlyConnection:
    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()): 
        if _is_write_sql(sql):
            raise RuntimeError("read-only DB context rejected write SQL")
        return self._conn.execute(sql, params)

    def cursor(self):
        return ReadOnlyCursor(self._conn.cursor())

    def commit(self) -> None:
        # A read-only context must not commit writes. Roll back to clear any accidental transaction.
        try:
            self._conn.rollback()
        except Exception:  # validator: allow-wide-except
            log.debug("read-only rollback on commit skipped", exc_info=True)

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


@contextmanager
def get_db_ro() -> Iterator[Any]:
    conn = get_connection()
    try:
        if is_postgres_enabled():
            try:
                conn.execute("SET TRANSACTION READ ONLY")
            except Exception:  # validator: allow-wide-except
                log.debug("postgres read-only transaction marker failed", exc_info=True)
        else:
            try:
                conn.execute("PRAGMA query_only=ON")
            except Exception:  # validator: allow-wide-except
                log.debug("sqlite query_only marker failed", exc_info=True)
        yield ReadOnlyConnection(conn)
    finally:
        try:
            conn.rollback()
        except Exception:  # validator: allow-wide-except
            log.debug("read-only rollback failed", exc_info=True)
        try:
            conn.close()
        except Exception:  # validator: allow-wide-except
            log.exception("DB close failed")
