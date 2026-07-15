from __future__ import annotations

from services.db import execute


def test_public_execute_materializes_rows_before_connection_closes():
    key = "test_db_execute_contract"
    execute(
        "INSERT INTO engine_state(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, "1", 1),
    )

    row = execute("SELECT value FROM engine_state WHERE key=?", (key,), fetchone=True)
    assert row["value"] == "1"

    rows = execute("SELECT key, value FROM engine_state WHERE key=?", (key,), fetchall=True)
    assert [(item["key"], item["value"]) for item in rows] == [(key, "1")]

    default_rows = execute("SELECT value FROM engine_state WHERE key=?", (key,))
    assert [item["value"] for item in default_rows] == ["1"]
