from __future__ import annotations

from services.db import db
from services.migrations import apply_all_migrations


def test_apply_all_migrations_on_clean_db_without_payments_table():
    with db() as conn:
        conn.execute("DROP TABLE IF EXISTS payments")
        apply_all_migrations(conn)
        applied = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    assert "payments_decision_attribution_v1" in applied


def test_apply_all_migrations_creates_practice_token_tables():
    with db() as conn:
        apply_all_migrations(conn)
        applied = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert "practice_token_economy_v1" in applied
    assert "practice_wallets" in tables
    assert "practice_ledger" in tables
    assert "practice_reservations" in tables
    assert "payment_token_grants" in tables
    assert "user_practice_preferences" in tables
