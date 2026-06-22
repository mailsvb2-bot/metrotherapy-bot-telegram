from __future__ import annotations

from services.db.core import tx, translate_sql_for_postgres


def test_sqlite_master_name_in_filter_keeps_postgres_placeholders():
    sql = "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?)"

    translated = translate_sql_for_postgres(sql)

    assert "information_schema.tables" in translated
    assert "table_name IN (%s,%s,%s,%s,%s)" in translated
    assert translated.count("%s") == 5


def test_sqlite_master_name_equals_filter_keeps_postgres_placeholder():
    sql = "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1"

    translated = translate_sql_for_postgres(sql)

    assert "information_schema.tables" in translated
    assert "table_name=%s" in translated
    assert translated.count("%s") == 1


def test_tx_scope_does_not_enter_or_close_connection():
    calls: list[str] = []

    class FakeConnection:
        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            calls.append("exit")
            return False

        def close(self):
            calls.append("close")

        def commit(self):
            calls.append("commit")

        def rollback(self):
            calls.append("rollback")

    conn = FakeConnection()

    with tx(conn) as scoped:
        assert scoped is conn

    assert calls == []
