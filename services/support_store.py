from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

"""Storage helpers for Support-AI.

The runtime supports SQLite for local tests and PostgreSQL in production through
``services.db`` compatibility APIs.
"""

from core.time_utils import utcnow_iso
from services.db import db


@dataclass(frozen=True)
class BodyAreaObservation:
    area: str
    created_at_utc: str


def fetch_recent_body_area_observations(
    user_id: int,
    *,
    limit: int = 30,
) -> list[BodyAreaObservation]:
    """Return newest body answers with timestamps for calendar-day reasoning."""
    bounded_limit = max(1, min(int(limit), 365))
    with db() as conn:
        rows = conn.execute(
            """
            SELECT area, created_at_utc
            FROM body_feedback
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(user_id), bounded_limit),
        ).fetchall()

    observations: list[BodyAreaObservation] = []
    for row in rows:
        if hasattr(row, "keys"):
            area = row["area"]
            created_at = row["created_at_utc"]
        else:
            area = row[0]
            created_at = row[1]
        observations.append(
            BodyAreaObservation(
                area=str(area or "").strip(),
                created_at_utc=str(created_at or "").strip(),
            )
        )
    return observations


def fetch_recent_body_areas(user_id: int, *, limit: int = 10) -> list[str]:
    """Backward-compatible newest body-area list without timestamps."""
    return [
        observation.area
        for observation in fetch_recent_body_area_observations(user_id, limit=limit)
    ]


def count_same_prefix_streak(values: list[str]) -> int:
    """Count equal newest-first values; kept for compatibility callers."""
    if not values:
        return 0
    first = values[0]
    n = 0
    for value in values:
        if value == first:
            n += 1
        else:
            break
    return n


def log_reaction(
    *,
    user_id: int,
    mode: str,
    area: str | None,
    note: str | None = None,
) -> None:
    """Записывает выбранный режим сопровождения (для диагностики и будущих метрик)."""
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO system_reactions_log(user_id, created_at_utc, mode, area, note)
                VALUES(?,?,?,?,?)
                """,
                (int(user_id), utcnow_iso(), str(mode), (str(area) if area else None), (str(note) if note else None)),
            )
    except (sqlite3.Error, TypeError, ValueError):
        logging.getLogger(__name__).exception("support_store: save_system_reaction failed")


def save_daily_state(
    *,
    user_id: int,
    day: str,
    kind: str,
    pre_score: int | None,
    post_score: int | None,
    area: str | None,
    mode: str,
    audio_id: str | None = None,
) -> None:
    """Сохраняет дневное состояние (идемпотентно по (user_id, day, kind))."""
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO user_daily_state(user_id, day, kind, pre_score, post_score, area, mode, audio_id, updated_at_utc)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, day, kind) DO UPDATE SET
                    pre_score=COALESCE(excluded.pre_score, user_daily_state.pre_score),
                    post_score=COALESCE(excluded.post_score, user_daily_state.post_score),
                    area=COALESCE(excluded.area, user_daily_state.area),
                    mode=excluded.mode,
                    audio_id=COALESCE(excluded.audio_id, user_daily_state.audio_id),
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    int(user_id),
                    str(day),
                    str(kind or ""),
                    int(pre_score) if pre_score is not None else None,
                    int(post_score) if post_score is not None else None,
                    (str(area) if area else None),
                    str(mode),
                    (str(audio_id) if audio_id else None),
                    utcnow_iso(),
                ),
            )
    except (sqlite3.Error, TypeError, ValueError):
        logging.getLogger(__name__).exception("support_store: save_system_reaction failed")
