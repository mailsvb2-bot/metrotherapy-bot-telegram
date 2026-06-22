from __future__ import annotations

import sqlite3

from services.db.schema._parts import part_01 as _p1
from services.db.schema._parts import part_04 as _p4


def ensure(c: sqlite3.Connection) -> None:
    """Gift codes and gift-related accounting."""
    _p1.ensure(c)
    _p4.ensure(c)
