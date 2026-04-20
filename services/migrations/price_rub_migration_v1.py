from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import migration_applied, mark_migration

NAME = "price_rub_migration_v1"


def apply(conn: sqlite3.Connection) -> None:
    """One-time migration: prices stored in kopecks -> rubles."""
    log = logging.getLogger(__name__)
    try:
        if migration_applied(conn, NAME):
            log.info("Price migration skipped (already applied): %s", NAME)
            return
    except sqlite3.Error:
        return

    log.info("Price migration start: %s", NAME)

    try:
        rows = conn.execute(
            "SELECT id, code, price FROM plans WHERE price IS NOT NULL AND price >= 50000 AND price % 100 = 0"
        ).fetchall()
    except sqlite3.Error:
        # If plans table doesn't exist yet, nothing to migrate.
        mark_migration(conn, NAME)
        return

    updated = 0
    for r in rows:
        try:
            pid, code, old_price = int(r[0]), str(r[1]), int(r[2])
        except (TypeError, ValueError, IndexError):
            continue
        new_price = old_price // 100
        if new_price <= 0:
            continue
        try:
            conn.execute(
                "UPDATE plans SET price=?, updated_at=datetime('now') WHERE id=?",
                (new_price, pid),
            )
            conn.execute(
                """
                INSERT INTO plan_price_history(plan_code, old_price, new_price, changed_at_utc, changed_by)
                VALUES(?, ?, ?, datetime('now'), NULL)
                """.strip(),
                (code, old_price, new_price),
            )
            updated += 1
        except sqlite3.Error:
            log.exception("Failed to migrate plan price for %s", code)

    try:
        conn.execute(
            "UPDATE selected_plan SET price = price/100 WHERE price IS NOT NULL AND price >= 50000 AND price % 100 = 0"
        )
    except sqlite3.Error:
        log.debug("selected_plan normalization skipped", exc_info=True)

    mark_migration(conn, NAME)
    log.info("Price migration marker set: %s", NAME)
    if updated:
        log.info("Price migration applied: %s plans updated", updated)
    else:
        # Nothing matched the heuristic (already migrated or no suitable rows)
        log.info("Price migration applied: nothing to update")