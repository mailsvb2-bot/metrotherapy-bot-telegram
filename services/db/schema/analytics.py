from __future__ import annotations

import sqlite3

from services.db.schema._parts import part_02 as _p2
from services.db.schema._parts import part_04 as _p4
from services.db.schema._parts import part_06 as _p6
from services.db.schema._parts import part_07 as _p7
from services.db.schema._parts import part_08_ai_economy as _p8


def ensure(c: sqlite3.Connection) -> None:
    """Analytics tables (demo/user logs/mood/state/behavior/SLA)."""
    _p2.ensure(c)
    _p4.ensure(c)
    _p6.ensure(c)
    _p7.ensure(c)
    _p8.ensure(c)
