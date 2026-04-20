from __future__ import annotations

import sqlite3
from services.schema_core import _cols

def ensure(c: sqlite3.Connection) -> None:
    """AI Economy Core tables: policy registry, decision rewards."""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS policy_registry(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_name TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            status TEXT NOT NULL, -- active/candidate/rolled_back
            activated_at_utc TEXT,
            meta TEXT
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_policy_registry_status ON policy_registry(status)")

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_rewards(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            correlation_id TEXT,
            reward_value REAL NOT NULL,
            money_value REAL NOT NULL,
            state_value REAL NOT NULL,
            retention_value REAL NOT NULL,
            window_sec INTEGER NOT NULL,
            computed_at_utc TEXT NOT NULL,
            meta TEXT
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_decision_rewards_decision ON decision_rewards(decision_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_decision_rewards_user ON decision_rewards(user_id)")
