from __future__ import annotations

import sqlite3

from services.db.schema._parts import part_01 as _p1
from services.db.schema._parts import part_07 as _p7


def ensure(c: sqlite3.Connection) -> None:
    """Jobs/scheduler/engine state tables."""
    _p1.ensure(c)
    _p7.ensure(c)
