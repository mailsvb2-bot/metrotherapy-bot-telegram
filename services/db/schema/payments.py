from __future__ import annotations

import sqlite3

from services.db.schema._parts import part_03 as _p3
from services.db.schema._parts import part_07 as _p7


def ensure(c: sqlite3.Connection) -> None:
    """Payments and payment idempotency events."""
    _p3.ensure(c)
    _p7.ensure(c)
