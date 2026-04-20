from __future__ import annotations

import sqlite3

from services.schema_core import _cols, _add_col


def ensure(c: sqlite3.Connection) -> None:
    """Ensure tables/columns/indexes exist.

    Sections: ENGINE STATE (v7.0): coarse locks / flags for idempotent scheduler & engine, --- Support-AI (сопровождение) ---, --- SLA метрики (UX guard) ---, --- История изменения цен тарифов ---, --- Гранулярные права админов (задаёт супер-админ) ---, Payments idempotency (Telegram successful_payment can be delivered more than once)
    """
    # ENGINE STATE (v7.0): coarse locks / flags for idempotent scheduler & engine
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS engine_state(
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at INTEGER
        )
        """
    )
    # NOTE: scheduled_jobs was replaced by DB-backed `jobs` table (see part_01 + migration).
    # Keep schema free of legacy scheduled_jobs to avoid drift.
    # --- Support-AI (сопровождение) ---
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_daily_state(
            user_id INTEGER,
            day TEXT,
            kind TEXT,
            pre_score INTEGER,
            post_score INTEGER,
            area TEXT,
            mode TEXT,
            audio_id TEXT,
            updated_at_utc TEXT,
            PRIMARY KEY(user_id, day, kind)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_dynamic_profile(
            user_id INTEGER PRIMARY KEY,
            same_area_days INTEGER DEFAULT 0,
            avg_delta_7d REAL,
            variance_7d REAL,
            last_area TEXT,
            updated_at_utc TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS system_reactions_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            created_at_utc TEXT,
            mode TEXT,
            area TEXT,
            note TEXT
        )
        """
    )

    # --- SLA метрики (UX guard) ---
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS sla_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            metric TEXT NOT NULL,
            value_ms INTEGER NOT NULL,
            ts REAL NOT NULL
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_sla_metrics_ts ON sla_metrics(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sla_metrics_metric_ts ON sla_metrics(metric, ts)")

    # --- История изменения цен тарифов ---
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_code TEXT NOT NULL,
            old_price INTEGER,
            new_price INTEGER NOT NULL,
            changed_at_utc TEXT NOT NULL,
            changed_by INTEGER
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_price_history_code_ts ON plan_price_history(plan_code, changed_at_utc)"
    )

    # --- Гранулярные права админов (задаёт супер-админ) ---
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_permissions (
            admin_id INTEGER NOT NULL,
            perm TEXT NOT NULL,
            allowed INTEGER NOT NULL DEFAULT 1,
            updated_at_utc TEXT,
            updated_by INTEGER,
            UNIQUE(admin_id, perm)
        )
        """
    )




    # Payments idempotency (Telegram successful_payment can be delivered more than once)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_events(
            payment_id TEXT PRIMARY KEY,
            telegram_payment_charge_id TEXT,
            provider_payment_charge_id TEXT,
            user_id INTEGER NOT NULL,
            kind TEXT,
            invoice_payload TEXT,
            amount INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
