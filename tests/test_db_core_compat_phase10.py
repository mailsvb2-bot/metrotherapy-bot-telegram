from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from services.db import core


class DriverError(Exception):
    pass


class RawCursor:
    def __init__(
        self,
        *,
        rowcount: int = -1,
        one: Any = None,
        all_rows: list[Any] | None = None,
        execute_error: BaseException | None = None,
        many_error: BaseException | None = None,
        fetchone_error: BaseException | None = None,
        fetchall_error: BaseException | None = None,
    ) -> None:
        self.rowcount = rowcount
        self.one = one
        self.all_rows = list(all_rows or [])
        self.execute_error = execute_error
        self.many_error = many_error
        self.fetchone_error = fetchone_error
        self.fetchall_error = fetchall_error
        self.execute_calls: list[tuple[str, Any]] = []
        self.executemany_calls: list[tuple[str, Any]] = []
        self.closed = False

    def execute(self, sql: str, params: Any) -> None:
        self.execute_calls.append((sql, params))
        if self.execute_error is not None:
            raise self.execute_error

    def executemany(self, sql: str, params: Any) -> None:
        self.executemany_calls.append((sql, params))
        if self.many_error is not None:
            raise self.many_error

    def fetchone(self) -> Any:
        if self.fetchone_error is not None:
            raise self.fetchone_error
        return self.one

    def fetchall(self) -> list[Any]:
        if self.fetchall_error is not None:
            raise self.fetchall_error
        return list(self.all_rows)

    def close(self) -> None:
        self.closed = True


class RawConnection:
    def __init__(
        self,
        cursor: RawCursor | None = None,
        *,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
    ) -> None:
        self.raw_cursor = cursor or RawCursor()
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def cursor(self) -> RawCursor:
        return self.raw_cursor

    def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self) -> None:
        self.close_calls += 1


def test_sqlite_exception_compatibility() -> None:
    original = sqlite3.OperationalError("already sqlite")
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        core._raise_sqlite_compat(original)
    assert exc_info.value is original

    for message in ("relation does not exist", "undefined table x", "undefined column y", "syntax error", "invalid input syntax"):
        with pytest.raises(sqlite3.OperationalError, match=message):
            core._raise_sqlite_compat(DriverError(message))

    for message in ("duplicate key", "unique constraint"):
        with pytest.raises(sqlite3.IntegrityError, match=message):
            core._raise_sqlite_compat(DriverError(message))

    with pytest.raises(sqlite3.DatabaseError, match="network broke"):
        core._raise_sqlite_compat(DriverError("network broke"))


def test_pg_row_index_and_mapping_access() -> None:
    row = core.PgRow({"a": 1, "b": 2})
    assert row[0] == 1
    assert row[1] == 2
    assert row["b"] == 2
    with pytest.raises(KeyError):
        _ = row[5]


def test_sql_classification_and_param_normalization() -> None:
    assert core._is_select_changes_sql(" SELECT changes() AS c ") is True
    assert core._is_select_changes_sql("SELECT 1") is False
    for sql in ("insert into x values(1)", " UPDATE x SET a=1", "DELETE FROM x", "REPLACE INTO x VALUES(1)"):
        assert core._is_dml_sql(sql) is True
    assert core._is_dml_sql("SELECT 1") is False

    assert core._normalize_params(None) == ()
    assert core._normalize_params([1, 2]) == (1, 2)
    assert core._normalize_params((1, 2)) == (1, 2)
    marker = object()
    assert core._normalize_params(marker) is marker


def test_cursor_synthetic_changes_and_fetching() -> None:
    raw = RawCursor()
    conn = core.PostgresCompatConnection(RawConnection(raw))
    conn.last_rowcount = 3
    cursor = core.PostgresCompatCursor(raw, conn)

    assert cursor.execute("SELECT changes() AS c") is cursor
    assert cursor.rowcount == 1
    assert cursor.fetchone()["c"] == 3
    assert cursor.fetchone() is None

    conn.last_rowcount = 4
    cursor.execute("SELECT changes()")
    assert cursor.fetchall() == [core.PgRow({"c": 4})]
    assert cursor.fetchall() == []


def test_cursor_execute_many_wrap_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = RawCursor(rowcount=2, one={"id": 7}, all_rows=[{"id": 7}, {"id": 8}])
    raw_conn = RawConnection(raw)
    conn = core.PostgresCompatConnection(raw_conn)
    cursor = core.PostgresCompatCursor(raw, conn)

    monkeypatch.setattr(core, "translate_sql_for_postgres", lambda sql: "translated:" + sql)
    cursor.execute("INSERT INTO x VALUES(?)", [1])
    assert raw.execute_calls == [("translated:INSERT INTO x VALUES(?)", (1,))]
    assert conn.last_rowcount == 2

    cursor.executemany("UPDATE x SET a=?", [[1], [2]])
    assert raw.executemany_calls[-1] == ("translated:UPDATE x SET a=?", [(1,), (2,)])
    assert conn.last_rowcount == 2

    cursor.execute("SELECT * FROM x")
    assert cursor.fetchone() == core.PgRow({"id": 7})
    assert cursor.fetchall() == [core.PgRow({"id": 7}), core.PgRow({"id": 8})]
    cursor.close()
    assert raw.closed is True


def test_cursor_maps_driver_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "translate_sql_for_postgres", lambda sql: sql)
    conn = core.PostgresCompatConnection(RawConnection())

    with pytest.raises(sqlite3.IntegrityError):
        core.PostgresCompatCursor(RawCursor(execute_error=DriverError("duplicate key")), conn).execute("INSERT x")
    with pytest.raises(sqlite3.OperationalError):
        core.PostgresCompatCursor(RawCursor(many_error=DriverError("undefined table")), conn).executemany("INSERT x", [()])
    with pytest.raises(sqlite3.DatabaseError):
        core.PostgresCompatCursor(RawCursor(fetchone_error=DriverError("socket")), conn).fetchone()
    with pytest.raises(sqlite3.DatabaseError):
        core.PostgresCompatCursor(RawCursor(fetchall_error=DriverError("socket")), conn).fetchall()


def test_connection_context_success_body_error_and_commit_error() -> None:
    raw = RawConnection()
    conn = core.PostgresCompatConnection(raw)
    with conn as entered:
        assert entered is conn
    assert raw.commit_calls == 1
    assert raw.close_calls == 1

    raw = RawConnection()
    conn = core.PostgresCompatConnection(raw)
    with pytest.raises(ValueError):
        with conn:
            raise ValueError("body")
    assert raw.rollback_calls == 1
    assert raw.close_calls == 1

    raw = RawConnection(commit_error=RuntimeError("commit"))
    conn = core.PostgresCompatConnection(raw)
    with pytest.raises(RuntimeError, match="commit"):
        with conn:
            pass
    assert raw.rollback_calls == 1
    assert raw.close_calls == 1


def test_connection_rollback_failures_and_reusable_close() -> None:
    raw = RawConnection(commit_error=RuntimeError("commit"), rollback_error=RuntimeError("rollback"))
    conn = core.PostgresCompatConnection(raw)
    with pytest.raises(RuntimeError, match="commit"):
        with conn:
            pass
    assert raw.close_calls == 1

    raw = RawConnection(rollback_error=RuntimeError("rollback"))
    conn = core.PostgresCompatConnection(raw)
    with pytest.raises(ValueError):
        with conn:
            raise ValueError("body")
    assert raw.close_calls == 1

    raw = RawConnection()
    reusable = core.PostgresCompatConnection(raw, reusable=True)
    reusable.close()
    assert raw.close_calls == 0
    reusable.force_close()
    assert raw.close_calls == 1


def test_connection_cursor_execute_commit_and_rollback() -> None:
    raw_cursor = RawCursor(rowcount=1)
    raw = RawConnection(raw_cursor)
    conn = core.PostgresCompatConnection(raw)
    assert isinstance(conn.cursor(), core.PostgresCompatCursor)
    assert conn.execute("SELECT 1") is not None
    conn.last_rowcount = 9
    conn.rollback()
    assert conn.last_rowcount == 0
    conn.commit()
    assert raw.commit_calls == 1


def test_wrap_pg_row() -> None:
    assert core._wrap_pg_row(None) is None
    assert core._wrap_pg_row({"x": 1}) == core.PgRow({"x": 1})
    marker = (1, 2)
    assert core._wrap_pg_row(marker) is marker


def test_qmark_translation_respects_quoted_literals() -> None:
    sql = "SELECT '?', \"?\", ? FROM x WHERE note='a?b' AND id=?"
    assert core._replace_qmark_placeholders(sql) == "SELECT '?', \"?\", %s FROM x WHERE note='a?b' AND id=%s"


def test_insert_translation_helpers() -> None:
    original = "SELECT 1"
    assert core._translate_insert_or_ignore(original) == original
    assert core._translate_insert_or_ignore("INSERT OR IGNORE INTO x(a,b) VALUES(?,?)") == (
        "INSERT INTO x(a,b) VALUES(?,?) ON CONFLICT DO NOTHING"
    )

    audio = core._translate_insert_or_replace("INSERT OR REPLACE INTO audio_cache VALUES(?,?,?,?)")
    assert "ON CONFLICT (path, kind) DO UPDATE" in audio
    assert "%s" in audio

    migrations = core._translate_insert_or_replace("INSERT OR REPLACE INTO schema_migrations VALUES(?)")
    assert "ON CONFLICT (name) DO UPDATE" in migrations
    assert core._translate_insert_or_replace(original) == original


def test_sqlite_master_translation_variants() -> None:
    assert core._translate_sqlite_master_tables_query("SELECT x") is None
    base = core._translate_sqlite_master_tables_query("SELECT name FROM sqlite_master WHERE type='table'")
    assert base is not None and "information_schema.tables" in base

    by_names = core._translate_sqlite_master_tables_query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)"
    )
    assert by_names is not None and "table_name IN (%s,%s)" in by_names

    by_name = core._translate_sqlite_master_tables_query(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?"
    )
    assert by_name is not None and "table_name=%s LIMIT 1" in by_name

    excluding_internal = core._translate_sqlite_master_tables_query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    assert excluding_internal is not None and "NOT LIKE 'sqlite_%'" in excluding_internal


def test_sql_literal_and_full_translation_matrix() -> None:
    assert core._sql_string_literal("a'b") == "'a''b'"
    assert core.translate_sql_for_postgres("") == ""
    assert core.translate_sql_for_postgres("BEGIN IMMEDIATE") == "BEGIN"
    assert core.translate_sql_for_postgres("PRAGMA cache_size") == "SELECT 1"
    assert core.translate_sql_for_postgres("select last_insert_rowid() as id") == "SELECT LASTVAL() AS id"

    pragma = core.translate_sql_for_postgres("PRAGMA table_info(users)")
    assert "information_schema.columns" in pragma
    assert "table_name='users'" in pragma
    assert core.translate_sql_for_postgres("PRAGMA table_info(users;drop)") == "SELECT 1 WHERE FALSE"

    translated = core.translate_sql_for_postgres(
        "CREATE TABLE x(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INT, note TEXT DEFAULT datetime('now'))"
    )
    assert "BIGSERIAL PRIMARY KEY" in translated
    assert "user_id BIGINT" in translated
    assert "chat_id BIGINT" in translated
    assert "CURRENT_TIMESTAMP" in translated

    epoch = core.translate_sql_for_postgres("SELECT strftime('%s','now'), ?")
    assert "EXTRACT(EPOCH FROM CURRENT_TIMESTAMP)::BIGINT" in epoch
    assert epoch.endswith("%s")
