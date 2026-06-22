from __future__ import annotations
import logging


import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from services.db import db


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log_event(user_id: int, name: str, meta: Any | None = None, *, conn=None):
    """
    Логирование событий.
    ВАЖНО: если вызывается внутри транзакции, передаём conn=conn, чтобы не ловить 'database is locked'.
    """
    payload = json.dumps(meta or {}, ensure_ascii=False)

    if conn is not None:
        conn.execute(
            "INSERT INTO events(user_id, name, meta, created_at) VALUES(?,?,?,?)",
            (user_id, name, payload, _now_utc_iso()),
        )
        return

    with db() as c:
        c.execute(
            "INSERT INTO events(user_id, name, meta, created_at) VALUES(?,?,?,?)",
            (user_id, name, payload, _now_utc_iso()),
        )


def funnel_counts(names: list[str]) -> dict[str, int]:
    """Возвращает количество уникальных пользователей по каждому событию.

    Используется в админ-панели для быстрой воронки.
    """
    if not names:
        return {}

    res: dict[str, int] = {n: 0 for n in names}
    q = (
        "SELECT name, COUNT(DISTINCT user_id) AS cnt "
        "FROM events "
        f"WHERE name IN ({','.join('?' for _ in names)}) "
        "GROUP BY name"
    )
    with db() as c:
        for row in c.execute(q, tuple(names)).fetchall():
            try:
                n = row[0]
                cnt = int(row[1] or 0)
            except (IndexError, TypeError, ValueError):
                logging.getLogger(__name__).exception("Bad row in events counts")
                continue
            if n in res:
                res[n] = cnt
    return res


def has_event_since(user_id: int, name: str, since_utc_iso: str) -> bool:
    """Есть ли у пользователя событие name, созданное не раньше since_utc_iso.

    since_utc_iso должен быть в формате ISO (как мы пишем в events.created_at).
    Функция нужна для идемпотентных проверок в автоворонке.
    """
    with db() as c:
        row = c.execute(
            "SELECT 1 FROM events WHERE user_id=? AND name=? AND created_at >= ? LIMIT 1",
            (int(user_id), str(name), str(since_utc_iso)),
        ).fetchone()
    return row is not None

from core.runtime.sovereignty.enforcement import get_current_token

# Cache PRAGMA table_info results per-process to avoid slow PRAGMA on every event write.
_EVENTS_COLS_CACHE: set[str] | None = None

def _events_cols(conn) -> set[str]:
    global _EVENTS_COLS_CACHE
    if _EVENTS_COLS_CACHE is None:
        try:
            _EVENTS_COLS_CACHE = {str(r[1]) for r in conn.execute("PRAGMA table_info(events)").fetchall()}
        except sqlite3.Error:
            _EVENTS_COLS_CACHE = {"user_id","name","meta","created_at"}
        except (IndexError, TypeError, ValueError):
            _EVENTS_COLS_CACHE = {"user_id","name","meta","created_at"}
    return _EVENTS_COLS_CACHE


def log_runtime_event(
    user_id: int,
    *,
    event_type: str,
    payload: Any | None = None,
    source: str = "telegram",
    correlation_id: str | None = None,
    decision_id: str | None = None,
    conn=None,
) -> None:
    """Event v2 per Constitution, but backward-compatible with legacy `events` table.

    Writes into `events` using only columns that actually exist in the DB schema.
    Never raises (best-effort): runtime must not crash because of analytics.
    """
    tok = get_current_token()

    did = decision_id or (getattr(tok, "decision_id", None) if tok else None)
    corr = correlation_id or (getattr(tok, "nonce", None) if tok else None)

    meta_obj = payload or {}
    payload_json = json.dumps(meta_obj, ensure_ascii=False)
    ts = _now_utc_iso()

    # Legacy columns (always present in our project DBs)
    name = str(event_type)

    def _write(c):
        cols = _events_cols(c)
        insert_cols = ["user_id", "name", "meta", "created_at"]
        values = [int(user_id), name, payload_json, ts]

        # Extended v2 columns if present
        if "event_type" in cols:
            insert_cols.append("event_type")
            values.append(name)
        if "source" in cols:
            insert_cols.append("source")
            values.append(str(source))
        if "payload" in cols:
            insert_cols.append("payload")
            values.append(payload_json)
        if "timestamp_utc" in cols:
            insert_cols.append("timestamp_utc")
            values.append(ts)
        if "decision_id" in cols:
            insert_cols.append("decision_id")
            values.append(did)
        if "correlation_id" in cols:
            insert_cols.append("correlation_id")
            values.append(corr)

        q = f"INSERT INTO events({','.join(insert_cols)}) VALUES({','.join('?' for _ in insert_cols)})"
        c.execute(q, tuple(values))

    try:
        if conn is not None:
            _write(conn)
            return
        with db() as c:
            _write(c)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("log_runtime_event failed (best-effort)")
    except (TypeError, ValueError, KeyError):
        logging.getLogger(__name__).exception("log_runtime_event failed (best-effort)")
