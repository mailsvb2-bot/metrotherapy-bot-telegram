from __future__ import annotations


import time
from typing import Optional

from services.db import execute
from services.db_writer import enqueue


def _now_ts() -> int:
    return int(time.time())


def get_flag(key: str) -> bool:
    row = execute("SELECT value FROM engine_state WHERE key=?", (key,), fetchone=True)
    if not row:
        return False
    return str(row["value"] or "0") == "1"


async def acquire_lock(key: str, *, ttl_sec: int = 60) -> bool:
    """Acquire a coarse lock stored in SQLite.

    Uses a single UPSERT with a conditional UPDATE and RETURNING.
    If lock cannot be acquired, returns False.
    """
    now = _now_ts()
    expires_before = now - int(ttl_sec)

    sql = (
        "INSERT INTO engine_state(key, value, updated_at) VALUES(?, '1', ?) "
        "ON CONFLICT(key) DO UPDATE SET value='1', updated_at=? "
        "WHERE engine_state.value='0' OR engine_state.updated_at < ? "
        "RETURNING key"
    )

    row = await enqueue(sql, (key, now, now, expires_before), fetchone=True)
    return bool(row)


async def release_lock(key: str) -> None:
    now = _now_ts()
    await enqueue(
        "INSERT INTO engine_state(key, value, updated_at) VALUES(?, '0', ?) "
        "ON CONFLICT(key) DO UPDATE SET value='0', updated_at=?",
        (key, now, now),
    )
