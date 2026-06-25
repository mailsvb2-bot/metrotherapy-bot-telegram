from __future__ import annotations

from services.db.core import translate_sql_for_postgres


def test_translate_sqlite_epoch_now_for_postgres() -> None:
    sql = "INSERT OR IGNORE INTO idempotency(user_id, key, created_at) VALUES(?,?,strftime('%s','now'))"

    translated = translate_sql_for_postgres(sql)

    assert "strftime" not in translated.lower()
    assert "EXTRACT(EPOCH FROM CURRENT_TIMESTAMP)::BIGINT" in translated
    assert "%s" in translated
    assert "ON CONFLICT DO NOTHING" in translated
