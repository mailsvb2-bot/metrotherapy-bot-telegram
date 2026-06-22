from __future__ import annotations

import sqlite3

from services.db.schema import create_or_update_tables  # re-export

__all__ = ["create_or_update_tables"]
