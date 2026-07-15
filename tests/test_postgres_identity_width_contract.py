from __future__ import annotations

from services.db.core import translate_sql_for_postgres
from services.migrations.postgres_identity_bigint_v1 import _IDENTITY_COLUMN


def test_postgres_ddl_promotes_telegram_identity_columns_to_bigint() -> None:
    translated = translate_sql_for_postgres(
        """
        CREATE TABLE sample(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER PRIMARY KEY,
            buyer_user_id INTEGER NOT NULL,
            recipient_user_id INT,
            chat_id INTEGER,
            requested_by INTEGER,
            amount INTEGER
        )
        """
    )

    assert "id BIGSERIAL PRIMARY KEY" in translated
    assert "user_id BIGINT PRIMARY KEY" in translated
    assert "buyer_user_id BIGINT NOT NULL" in translated
    assert "recipient_user_id BIGINT" in translated
    assert "chat_id BIGINT" in translated
    assert "requested_by BIGINT" in translated
    assert "amount INTEGER" in translated


def test_identity_migration_column_filter_is_narrow() -> None:
    assert _IDENTITY_COLUMN.fullmatch("user_id")
    assert _IDENTITY_COLUMN.fullmatch("buyer_user_id")
    assert _IDENTITY_COLUMN.fullmatch("telegram_chat_id")
    assert _IDENTITY_COLUMN.fullmatch("admin_id")
    assert _IDENTITY_COLUMN.fullmatch("requested_by")
    assert not _IDENTITY_COLUMN.fullmatch("payment_id")
    assert not _IDENTITY_COLUMN.fullmatch("amount")
