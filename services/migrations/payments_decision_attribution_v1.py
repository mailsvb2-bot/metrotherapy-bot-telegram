from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import migration_applied, mark_migration, table_exists

NAME = "payments_decision_attribution_v1"
logger = logging.getLogger(__name__)

def apply(conn: sqlite3.Connection) -> None:
    logger.info("Migration start: %s", NAME)
    if migration_applied(conn, NAME):
        logger.info("Migration already applied: %s", NAME)
        return
    if not table_exists(conn, "payments"):
        logger.info("Migration skipped (payments table missing): %s", NAME)
        mark_migration(conn, NAME)
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()}
    if "decision_id" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN decision_id TEXT")
    if "correlation_id" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN correlation_id TEXT")
    mark_migration(conn, NAME)
    logger.info("Migration applied: %s", NAME)
