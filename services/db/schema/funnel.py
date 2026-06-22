from __future__ import annotations

import sqlite3

from services.db.schema._parts import part_03 as _p3
from services.db.schema._parts import part_05 as _p5
from services.db.schema._parts import part_06 as _p6


def ensure(c: sqlite3.Connection) -> None:
    """Funnel, copies and personalization state."""
    _p3.ensure(c)
    _p5.ensure(c)
    _p6.ensure(c)
