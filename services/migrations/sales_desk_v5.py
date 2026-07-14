from __future__ import annotations

import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

MIGRATION_NAME = "sales_desk_v5"


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, MIGRATION_NAME):
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_key TEXT NOT NULL UNIQUE,
            user_id BIGINT UNIQUE,
            account_id BIGINT,
            display_name TEXT NOT NULL,
            username TEXT,
            source TEXT NOT NULL DEFAULT 'organic',
            campaign TEXT,
            creative TEXT,
            stage TEXT NOT NULL DEFAULT 'new',
            stage_source TEXT NOT NULL DEFAULT 'auto',
            assigned_to BIGINT,
            next_contact_at TEXT,
            last_contact_at TEXT,
            last_activity_at TEXT,
            revenue_minor BIGINT NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'RUB',
            closed_reason TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (stage IN ('new','contacted','qualified','checkout','won','lost')),
            CHECK (stage_source IN ('auto','manual')),
            CHECK (revenue_minor >= 0),
            CHECK (version >= 1)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_leads_stage_updated ON sales_leads(stage, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_leads_owner_stage ON sales_leads(assigned_to, stage)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_leads_follow_up ON sales_leads(next_contact_at, stage)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_lead_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id BIGINT NOT NULL,
            author_id BIGINT NOT NULL,
            note_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(lead_id) REFERENCES sales_leads(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_lead_notes_lead ON sales_lead_notes(lead_id, id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_lead_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id BIGINT NOT NULL,
            event_type TEXT NOT NULL,
            actor_id BIGINT NOT NULL,
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(lead_id) REFERENCES sales_leads(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_lead_audit_lead ON sales_lead_audit(lead_id, id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_outbound_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL UNIQUE,
            lead_id BIGINT NOT NULL,
            actor_id BIGINT NOT NULL,
            platform TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            message_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'prepared',
            provider_message_id TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sent_at TEXT,
            FOREIGN KEY(lead_id) REFERENCES sales_leads(id),
            CHECK (platform IN ('telegram')),
            CHECK (status IN ('prepared','sent','failed','uncertain'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_outbound_lead ON sales_outbound_messages(lead_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_outbound_status ON sales_outbound_messages(status, updated_at)"
    )

    mark_migration(conn, MIGRATION_NAME)
