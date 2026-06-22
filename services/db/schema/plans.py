from __future__ import annotations

import sqlite3

from services.db.schema._parts import part_02 as _p2
from services.db.schema._parts import part_07 as _p7


def ensure(c: sqlite3.Connection) -> None:
    """Plans/selected_plan and price history (semantic entrypoint).

    Internally delegates to stable idempotent DDL segments.
    """
    _p2.ensure(c)
    _p7.ensure(c)
