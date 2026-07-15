from __future__ import annotations

import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

MIGRATION_NAME = "sales_desk_revenue_v6"


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, MIGRATION_NAME):
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_lead_revenue (
            user_id BIGINT NOT NULL,
            currency TEXT NOT NULL,
            amount_units BIGINT NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, currency),
            CHECK (amount_units >= 0)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_lead_revenue_user ON sales_lead_revenue(user_id)"
    )
    # Preserve the historical RUB mirror. Non-RUB values were previously mixed
    # into one field and cannot be separated reliably, so they are deliberately
    # not backfilled as invented accounting data.
    conn.execute(
        """
        INSERT INTO sales_lead_revenue(user_id, currency, amount_units, updated_at)
        SELECT user_id, 'RUB', revenue_minor, updated_at
        FROM sales_leads
        WHERE user_id IS NOT NULL AND currency='RUB' AND revenue_minor > 0
        ON CONFLICT DO NOTHING
        """
    )
    mark_migration(conn, MIGRATION_NAME)
