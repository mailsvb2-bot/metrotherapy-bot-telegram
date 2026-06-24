from __future__ import annotations

from services.db.core import translate_sql_for_postgres


def test_like_pattern_is_bound_param_for_postgres_translation():
    sql = "SELECT COUNT(DISTINCT user_id) AS c FROM payments WHERE payload NOT LIKE ?"

    assert translate_sql_for_postgres(sql) == (
        "SELECT COUNT(DISTINCT user_id) AS c FROM payments WHERE payload NOT LIKE %s"
    )
