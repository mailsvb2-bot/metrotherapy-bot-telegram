from __future__ import annotations

import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

MIGRATION_NAME = "growth_apply_gateway_v3"


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, MIGRATION_NAME):
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_apply_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_key TEXT NOT NULL UNIQUE,
            action_type TEXT NOT NULL,
            target_platform TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review',
            mode TEXT NOT NULL DEFAULT 'approval_only',
            dispatch_allowed INTEGER NOT NULL DEFAULT 0,
            requested_by INTEGER NOT NULL,
            requested_at TEXT NOT NULL,
            decided_by INTEGER,
            decided_at TEXT,
            decision_reason TEXT,
            expires_at TEXT,
            CHECK (status IN ('pending_review','approved','rejected','expired')),
            CHECK (mode = 'approval_only'),
            CHECK (dispatch_allowed = 0)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_growth_apply_requests_status ON growth_apply_requests(status, requested_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_apply_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            actor_id INTEGER NOT NULL,
            before_status TEXT,
            after_status TEXT,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(request_id) REFERENCES growth_apply_requests(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_growth_apply_audit_request ON growth_apply_audit(request_id, id)"
    )
    mark_migration(conn, MIGRATION_NAME)
