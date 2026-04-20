from __future__ import annotations

import sqlite3

from . import analytics, funnel, gifts, jobs, payments, plans, settings, users

# Execution order matters: base entities first, then dependent tables.
PARTS = [
    users,
    plans,
    payments,
    gifts,
    funnel,
    analytics,
    jobs,
    settings,
]


def create_or_update_tables(c: sqlite3.Connection) -> None:
    """Create tables and add missing columns (idempotent)."""
    for p in PARTS:
        p.ensure(c)
