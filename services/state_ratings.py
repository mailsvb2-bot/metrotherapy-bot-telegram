from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config.settings import settings
from services.db import db


log = logging.getLogger(__name__)


def add_rating(user_id: int, rating: int, *, created_at_utc: str | None = None) -> bool:
    """Сохранить быструю оценку состояния.

    Это отдельный поток данных (не mood_sessions): пользователь может поставить
    оценку в любой момент, и она должна сохраниться даже если он не строит график.
    """
    try:
        r = int(rating)
    except (TypeError, ValueError):
        return False
    if r < 1 or r > 10:
        return False

    ts = created_at_utc
    if not ts:
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO state_ratings(user_id, rating, created_at_utc) VALUES(?,?,?)",
                (int(user_id), int(r), str(ts)),
            )
        return True
    except sqlite3.Error:
        log.exception("Failed to insert state rating")
        return False


def _local_day_utc_bounds(day: str) -> tuple[str, str] | None:
    """Convert a configured local calendar day into a half-open UTC interval."""
    try:
        local_date = datetime.strptime(str(day), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None

    timezone_name = str(getattr(settings, "TIMEZONE", "Europe/Moscow") or "Europe/Moscow")
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        log.error("Configured timezone is unavailable: %s; using UTC", timezone_name)
        local_tz = timezone.utc

    start_local = datetime.combine(local_date, time.min, tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        end_local.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
    )


def series(user_id: int, *, day: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Return the newest bounded rating history in chronological order.

    ``day`` is interpreted in the configured user-facing timezone and converted
    to UTC bounds. Selecting newest rows before applying ``LIMIT`` prevents long
    histories from freezing charts on their oldest values.
    """
    try:
        bounded_limit = max(1, min(int(limit), 10_000))
    except (TypeError, ValueError):
        log.warning("Invalid state rating limit; using default 200")
        bounded_limit = 200

    q = "SELECT rating, created_at_utc FROM state_ratings WHERE user_id=?"
    params: list[Any] = [int(user_id)]
    if day:
        bounds = _local_day_utc_bounds(str(day))
        if bounds is None:
            log.warning("Invalid local state-rating day: %r", day)
            return []
        start_utc, end_utc = bounds
        q += " AND created_at_utc>=? AND created_at_utc<?"
        params.extend((start_utc, end_utc))
    q += " ORDER BY created_at_utc DESC LIMIT ?"
    params.append(bounded_limit)

    try:
        with db() as conn:
            rows = conn.execute(q, tuple(params)).fetchall()
    except sqlite3.Error:
        log.exception("Failed to read state ratings")
        return []

    out: list[dict[str, Any]] = []
    for r in reversed(rows or []):
        try:
            rating = r[0] if not hasattr(r, "keys") else r["rating"]
            created = r[1] if not hasattr(r, "keys") else r["created_at_utc"]
            out.append({"rating": int(rating), "created": str(created)})
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    return out
