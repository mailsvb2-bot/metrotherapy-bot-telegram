from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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


def series(user_id: int, *, day: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Вернуть ряд оценок для графика.

    day: YYYY-MM-DD (в локальной логике анализа мы уже фильтруем по локальному дню,
    но в БД храним UTC. Здесь фильтр по дню применяется по строке UTC-даты.
    """
    q = "SELECT rating, created_at_utc FROM state_ratings WHERE user_id=?"
    params: list[Any] = [int(user_id)]
    if day:
        # упрощённый фильтр: по префиксу даты в ISO
        q += " AND substr(created_at_utc,1,10)=?"
        params.append(str(day))
    q += " ORDER BY created_at_utc ASC"
    if limit:
        q += " LIMIT ?"
        params.append(int(limit))

    try:
        with db() as conn:
            rows = conn.execute(q, tuple(params)).fetchall()
    except sqlite3.Error:
        log.exception("Failed to read state ratings")
        return []

    out: list[dict[str, Any]] = []
    for r in rows or []:
        try:
            rating = r[0] if not hasattr(r, "keys") else r["rating"]
            created = r[1] if not hasattr(r, "keys") else r["created_at_utc"]
            out.append({"rating": int(rating), "created": str(created)})
        except (TypeError, ValueError, KeyError):
            continue
        except IndexError:
            continue
    return out
