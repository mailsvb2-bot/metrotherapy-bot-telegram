from __future__ import annotations

"""Telegram file_id cache for audio.

Important: Runtime modules must NOT execute DDL (CREATE/ALTER). All schema is created
in services/schema_tables.py during init_db().

This module is a thin wrapper around the `audio_cache` table:

    audio_cache(path TEXT, kind TEXT, file_id TEXT, updated_at_utc TEXT, PRIMARY KEY(path, kind))
"""

import sqlite3
from pathlib import Path
from typing import Any, Optional

from core.time_utils import utc_now
from services.db import get_db


def _table_missing(e: Exception) -> bool:
    msg = str(e).lower()
    return "no such table" in msg or "does not exist" in msg


def _key_from_path_kind(file_path: Any, kind: str) -> tuple[str, str]:
    p = Path(file_path)
    # we store relative name to keep db small; kind separates demo/work/home etc.
    return (p.name, str(kind))


def get_file_id(conn: sqlite3.Connection, path: str, kind: str) -> Optional[str]:
    try:
        row = conn.execute(
            "SELECT file_id FROM audio_cache WHERE path=? AND kind=?",
            (str(path), str(kind)),
        ).fetchone()
    except sqlite3.Error as e:
        if _table_missing(e):
            return None
        raise
    if not row:
        return None
    return row[0] if not hasattr(row, "keys") else row["file_id"]


def set_file_id(conn: sqlite3.Connection, path: str, kind: str, file_id: str) -> None:
    try:
        conn.execute(
            "INSERT OR REPLACE INTO audio_cache(path, kind, file_id, updated_at_utc) VALUES(?,?,?,?)",
            (str(path), str(kind), str(file_id), utc_now().replace(microsecond=0).isoformat()),
        )
    except sqlite3.Error as e:
        if _table_missing(e):
            # If schema wasn't initialized, silently skip caching.
            return
        raise


# --- Backward/handler-compatible API ---

def get_cached_file_id(file_path_or_conn: Any, kind_or_key: str) -> Optional[str]:
    """Compatibility wrapper.

    Supports:
    - get_cached_file_id(conn, key)            # key is treated as path
    - get_cached_file_id(file_path, kind)
    """
    first = file_path_or_conn
    second = kind_or_key
    if hasattr(first, "execute"):
        # legacy: (conn, key) where key was either:
        #   - "kind:filename" (older wrapper)
        #   - or just "filename"
        conn = first
        key = str(second)
        if ":" in key:
            kind, path = key.split(":", 1)
        else:
            path, kind = key, "*"
        return get_file_id(conn, path, kind)

    path, kind = _key_from_path_kind(first, second)
    with get_db() as conn:
        return get_file_id(conn, path, kind)


def save_cached_file_id(file_path_or_conn: Any, kind_or_key: str, file_id: str) -> None:
    """Compatibility wrapper.

    Supports:
    - save_cached_file_id(conn, key, file_id)  # key treated as path
    - save_cached_file_id(file_path, kind, file_id)
    """
    first = file_path_or_conn
    second = kind_or_key
    if hasattr(first, "execute"):
        conn = first
        key = str(second)
        if ":" in key:
            kind, path = key.split(":", 1)
        else:
            path, kind = key, "*"
        set_file_id(conn, path, kind, file_id)
        return

    path, kind = _key_from_path_kind(first, second)
    with get_db() as conn:
        set_file_id(conn, path, kind, file_id)
        conn.commit()
