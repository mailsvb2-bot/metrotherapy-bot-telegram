from __future__ import annotations

from datetime import datetime, timezone


def now_utc_iso_sec() -> str:
    """UTC now ISO8601 rounded to seconds.

    Используем для анти-спама по клику (например, demo), где допустим ключ по секундам.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sid_key(session_id: int | str) -> str:
    """Канонический scheduled_at для событий, привязанных к сессии."""
    return f"sid:{int(session_id)}"


def wall_key(run_at: int) -> str:
    """Канонический scheduled_at для wall-clock событий (секунды Unix)."""
    return str(int(run_at))
