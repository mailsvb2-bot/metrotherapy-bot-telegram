from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from services.db.runtime import CONFIG, is_postgres_enabled, postgres_driver_error_hint

log = logging.getLogger(__name__)

try:
    from core.paths import ROOT as PROJECT_ROOT, DB_PATH, DATABASE_URL
except ImportError:  # pragma: no cover
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    DB_PATH = PROJECT_ROOT / "data.db"
    DATABASE_URL = ""


def _raise_sqlite_compat(exc: Exception):
    msg = str(exc)
    if isinstance(exc, sqlite3.Error):
        raise exc
    text = msg.lower()
    if 'does not exist' in text or 'undefined table' in text or 'undefined column' in text:
        raise sqlite3.OperationalError(msg) from exc
    if 'duplicate key' in text or 'unique constraint' in text:
        raise sqlite3.IntegrityError(msg) from exc
    if 'syntax error' in text or 'invalid input syntax' in text:
        raise sqlite3.OperationalError(msg) from exc
    raise sqlite3.DatabaseError(msg) from exc


class PgRow(dict):
    """Small compatibility shim that behaves close enough to sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            try:
                return list(self.values())[key]
            except IndexError as exc:
                raise KeyError(key) from exc
        return super().__getitem__(key)


def _is_select_changes_sql(sql: str) -> bool:
    """SQLite compatibility: SELECT changes() [AS c].

    Some legacy service code uses SQLite's changes() function after INSERT OR
    IGNORE / UPDATE to detect whether exactly one row was changed. Postgres has
    no such SQL function; the canonical DB compatibility layer owns this bridge.
    """
    return bool(re.match(r"(?is)^\s*SELECT\s+changes\s*\(\s*\)\s*(?:AS\s+\w+)?\s*$", sql or ""))


def _is_dml_sql(sql: str) -> bool:
    s = (sql or "").lstrip().upper()
    return s.startswith("INSERT") or s.startswith("UPDATE") or s.startswith("DELETE") or s.startswith("REPLACE")


class PostgresCompatCursor:
    def __init__(self, cursor, conn: "PostgresCompatConnection"):
        self._cursor = cursor
        self._conn = conn
        self._synthetic_rows: list[PgRow] | None = None
        self.rowcount = -1

    def execute(self, sql: str, params: Sequence[Any] = ()): 
        if _is_select_changes_sql(sql):
            self._synthetic_rows = [PgRow({"c": int(getattr(self._conn, "last_rowcount", 0) or 0)})]
            self.rowcount = 1
            return self

        self._synthetic_rows = None
        translated = translate_sql_for_postgres(sql)
        try:
            self._cursor.execute(translated, _normalize_params(params))
        except Exception as exc:  # validator: allow-wide-except
            _raise_sqlite_compat(exc)
        self.rowcount = getattr(self._cursor, "rowcount", -1)
        if _is_dml_sql(sql):
            self._conn.last_rowcount = max(int(self.rowcount or 0), 0)
        return self

    def executemany(self, sql: str, seq_of_params):
        self._synthetic_rows = None
        translated = translate_sql_for_postgres(sql)
        try:
            self._cursor.executemany(translated, [_normalize_params(p) for p in seq_of_params])
        except Exception as exc:  # validator: allow-wide-except
            _raise_sqlite_compat(exc)
        self.rowcount = getattr(self._cursor, "rowcount", -1)
        if _is_dml_sql(sql):
            self._conn.last_rowcount = max(int(self.rowcount or 0), 0)
        return self

    def fetchone(self):
        if self._synthetic_rows is not None:
            if not self._synthetic_rows:
                return None
            return self._synthetic_rows.pop(0)
        try:
            row = self._cursor.fetchone()
        except Exception as exc:  # validator: allow-wide-except
            _raise_sqlite_compat(exc)
        return _wrap_pg_row(row)

    def fetchall(self):
        if self._synthetic_rows is not None:
            rows = self._synthetic_rows
            self._synthetic_rows = []
            return rows
        try:
            rows = self._cursor.fetchall()
        except Exception as exc:  # validator: allow-wide-except
            _raise_sqlite_compat(exc)
        return [_wrap_pg_row(r) for r in rows]

    def close(self):
        self._cursor.close()


class PostgresCompatConnection:
    def __init__(self, conn):
        self._conn = conn
        self.last_rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self.commit()
            except Exception as commit_exc:  # validator: allow-wide-except
                try:
                    self.rollback()
                except Exception:  # validator: allow-wide-except
                    logging.getLogger(__name__).exception("Postgres rollback failed after commit error")
                logging.getLogger(__name__).exception("Postgres commit failed on context exit")
                self.close()
                raise commit_exc
        else:
            try:
                self.rollback()
            except Exception:  # validator: allow-wide-except
                logging.getLogger(__name__).exception("Postgres rollback failed on context exit")
        self.close()
        return False

    def cursor(self):
        return PostgresCompatCursor(self._conn.cursor(), self)

    def execute(self, sql: str, params: Sequence[Any] = ()): 
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()
        self.last_rowcount = 0

    def close(self):
        self._conn.close()


def _wrap_pg_row(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return PgRow(row)
    return row


def _normalize_params(params: Sequence[Any] | None):
    if params is None:
        return ()
    if isinstance(params, (list, tuple)):
        return tuple(params)
    return params


def _replace_qmark_placeholders(sql: str) -> str:
    out = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == '?' and not in_single and not in_double:
            out.append('%s')
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _translate_insert_or_ignore(sql: str) -> str:
    m = re.match(r"(?is)^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+([\w_]+)\s*(\([^)]*\))\s*(VALUES\s*\(.*\))\s*$", sql.strip())
    if not m:
        return sql
    table, cols, values = m.groups()
    return f"INSERT INTO {table}{cols} {values} ON CONFLICT DO NOTHING"


def _translate_insert_or_replace(sql: str) -> str:
    stripped = sql.strip()
    low = stripped.lower()
    if low.startswith('insert or replace into audio_cache'):
        return _replace_qmark_placeholders(
            "INSERT INTO audio_cache(path, kind, file_id, updated_at_utc) VALUES(?,?,?,?) "
            "ON CONFLICT (path, kind) DO UPDATE SET "
            "file_id=EXCLUDED.file_id, updated_at_utc=EXCLUDED.updated_at_utc"
        )
    if low.startswith('insert or replace into schema_migrations'):
        return _replace_qmark_placeholders(
            "INSERT INTO schema_migrations(name, applied_at_utc) VALUES(?, CURRENT_TIMESTAMP) "
            "ON CONFLICT (name) DO UPDATE SET applied_at_utc=EXCLUDED.applied_at_utc"
        )
    return sql


def _translate_sqlite_master_tables_query(s: str) -> str | None:
    if not re.match(r"(?is)^SELECT\s+name\s+FROM\s+sqlite_master\s+WHERE\s+type='table'", s):
        return None

    base = (
        "SELECT table_name AS name FROM information_schema.tables "
        "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
    )

    if re.search(r"(?is)\bname\s+IN\s*\(", s):
        placeholder_count = s.count("?")
        if placeholder_count > 0:
            placeholders = ",".join("%s" for _ in range(placeholder_count))
            return f"{base} AND table_name IN ({placeholders})"

    if re.search(r"(?is)\bname\s*=\s*\?", s):
        return f"{base} AND table_name=%s LIMIT 1"

    if re.search(r"(?is)\bname\s+NOT\s+LIKE\s+'sqlite_%'", s):
        return f"{base} AND table_name NOT LIKE 'sqlite_%'"

    return base


def translate_sql_for_postgres(sql: str) -> str:
    s = (sql or '').strip()
    if not s:
        return sql

    if re.match(r"(?is)^BEGIN\s+IMMEDIATE\s*$", s):
        return 'BEGIN'

    pragma_match = re.match(r"(?is)^PRAGMA\s+table_info\(([^)]+)\)\s*$", s)
    if pragma_match:
        table = pragma_match.group(1).strip().strip('"`[]')
        return (
            "SELECT ordinal_position - 1 AS cid, column_name AS name, data_type AS type, "
            "CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull, column_default AS dflt_value, "
            "CASE WHEN position('nextval' in coalesce(column_default, '')) > 0 THEN 1 ELSE 0 END AS pk "
            "FROM information_schema.columns "
            f"WHERE table_schema=current_schema() AND table_name='{table}' "
            "ORDER BY ordinal_position"
        )

    sqlite_master_tables_query = _translate_sqlite_master_tables_query(s)
    if sqlite_master_tables_query is not None:
        return sqlite_master_tables_query

    if s.upper().startswith('PRAGMA '):
        return 'SELECT 1'
    if s.lower() == 'select last_insert_rowid() as id':
        return 'SELECT LASTVAL() AS id'

    s = _translate_insert_or_replace(s)
    s = _translate_insert_or_ignore(s)
    s = s.replace("datetime('now')", 'CURRENT_TIMESTAMP')

    # DDL compatibility
    # SQLite INTEGER PRIMARY KEY behaves like an auto-generated rowid, so in Postgres
    # it must map to BIGSERIAL PRIMARY KEY rather than plain BIGINT PRIMARY KEY.
    s = re.sub(r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b', 'BIGSERIAL PRIMARY KEY', s, flags=re.IGNORECASE)
    s = re.sub(r'\bINT\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b', 'BIGSERIAL PRIMARY KEY', s, flags=re.IGNORECASE)
    s = re.sub(r'\bINTEGER\s+PRIMARY\s+KEY\b', 'BIGSERIAL PRIMARY KEY', s, flags=re.IGNORECASE)
    s = re.sub(r'\bINT\s+PRIMARY\s+KEY\b', 'BIGSERIAL PRIMARY KEY', s, flags=re.IGNORECASE)
    s = re.sub(r'\bAUTOINCREMENT\b', '', s, flags=re.IGNORECASE)

    s = _replace_qmark_placeholders(s)
    return s


def _load_psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg, dict_row
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(postgres_driver_error_hint()) from exc


def get_connection():
    if is_postgres_enabled():
        psycopg, dict_row = _load_psycopg()
        conn = psycopg.connect(DATABASE_URL, autocommit=False, row_factory=dict_row)
        return PostgresCompatConnection(conn)

    try:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA busy_timeout=10000;")
        conn.execute("PRAGMA foreign_keys=ON;")
    except sqlite3.Error as e:
        log.warning("PRAGMA init failed: %s", e)
    return conn


def _is_write_sql(sql: str) -> bool:
    s = (sql or "").lstrip().upper()
    return (
        s.startswith("INSERT")
        or s.startswith("UPDATE")
        or s.startswith("DELETE")
        or s.startswith("REPLACE")
        or s.startswith("CREATE")
        or s.startswith("DROP")
        or s.startswith("ALTER")
    )


@contextmanager
def get_db_ro() -> Iterator[Any]:
    conn = get_connection()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # validator: allow-wide-except
            logging.getLogger(__name__).exception("DB close failed")


@contextmanager
def get_db() -> Iterator[Any]:
    conn = get_connection()
    try:
        yield conn
        try:
            conn.commit()
        except Exception:  # validator: allow-wide-except
            logging.getLogger(__name__).exception("DB commit failed")
            try:
                conn.rollback()
            except Exception:  # validator: allow-wide-except
                logging.getLogger(__name__).exception("DB rollback after commit failure failed")
            raise
    finally:
        try:
            conn.close()
        except Exception:  # validator: allow-wide-except
            logging.getLogger(__name__).exception("DB close failed")


@contextmanager
def db() -> Iterator[Any]:
    with get_db() as conn:
        yield conn


def write(sql: str, params: tuple[Any, ...] = ()) -> int:
    with db() as conn:
        cur = conn.execute(sql, params)
        if is_postgres_enabled():
            return int(getattr(cur, 'rowcount', 0) or 0)
        try:
            return int(cur.rowcount)
        except (AttributeError, TypeError):
            return 0


def execute(sql: str, params: tuple[Any, ...] = ()):
    with db() as conn:
        return conn.execute(sql, params)


def tx(conn):
    return conn


def was_delivered(user_id: int, key: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
            (user_id, key),
        ).fetchone()
        return bool(row)


def mark_delivery_once(user_id: int, key: str) -> bool:
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO idempotency(user_id, key, created_at) VALUES(?,?,strftime('%s','now'))",
            (user_id, key),
        )
        row = conn.execute("SELECT changes() AS c").fetchone()
        return int(row["c"] if hasattr(row, "keys") else row[0]) == 1


def unmark_delivery(user_id: int, key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM idempotency WHERE user_id=? AND key=?", (user_id, key))
