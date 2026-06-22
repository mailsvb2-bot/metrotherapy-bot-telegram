from __future__ import annotations

import sqlite3

from services.db.schema._parts import part_02 as _p2
from services.db.schema._parts import part_03 as _p3
from services.db.schema._parts import part_05 as _p5
from services.db.schema._parts import part_07 as _p7


def ensure(c: sqlite3.Connection) -> None:
    """Settings/preferences/cache and admin permissions."""
    _p2.ensure(c)
    _p3.ensure(c)
    _p5.ensure(c)
    _p7.ensure(c)
