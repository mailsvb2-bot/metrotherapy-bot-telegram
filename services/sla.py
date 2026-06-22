from __future__ import annotations

import logging
import time
import sqlite3
from typing import Optional

from services.db import get_db


def _table_missing(e: Exception) -> bool:
    msg = str(e).lower()
    return "no such table" in msg or "does not exist" in msg

log = logging.getLogger(__name__)

_last_warn_ts: float = 0.0


def _warn_rate_limited(msg: str) -> None:
    """Не спамим логи, но оставляем след для диагностики."""
    global _last_warn_ts
    now = time.time()
    if now - _last_warn_ts < 60.0:
        return
    _last_warn_ts = now
    log.warning(msg, exc_info=True)

def record(user_id: int | None, metric: str, value_ms: float) -> None:
    """Записать метрику SLA (миллисекунды). Best-effort, не ломает UX."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO sla_metrics(user_id, metric, value_ms, ts) VALUES(?,?,?,?)",
                (user_id, metric, int(value_ms), time.time())
            )
            conn.commit()
    except sqlite3.Error as e:
        if _table_missing(e):
            # schema should be initialized in init_db(); never break UX if it's not
            return
        _warn_rate_limited("SLA metrics write failed (sqlite3.Error)")
        return
