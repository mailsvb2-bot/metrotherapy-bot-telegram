from __future__ import annotations

import sqlite3

from services.db.schema._parts import part_01 as _p


def ensure(c: sqlite3.Connection) -> None:
    """Users/subscriptions/events/referrals/progress (+ a few legacy tables)."""
    _p.ensure(c)
