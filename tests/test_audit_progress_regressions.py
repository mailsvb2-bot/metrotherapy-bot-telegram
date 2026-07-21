from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from handlers import start
from services import mood, state_ratings, support_ai
from services.support_store import BodyAreaObservation


def _db_context(conn: sqlite3.Connection):
    @contextmanager
    def _db():
        yield conn

    return _db


def test_mood_series_selects_latest_window_and_restores_chronology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE mood_sessions(
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            day TEXT NOT NULL,
            pre_score INTEGER,
            post_score INTEGER,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    for session_id in range(1, 16):
        conn.execute(
            """
            INSERT INTO mood_sessions(
                id, user_id, kind, day, pre_score, post_score, created_at_utc
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                session_id,
                7,
                "work",
                f"2026-07-{session_id:02d}",
                session_id,
                session_id + 1,
                f"2026-07-{session_id:02d}T08:00:00+00:00",
            ),
        )
    conn.commit()
    monkeypatch.setattr(mood, "db", _db_context(conn))

    rows = mood.series(7, kind="work", limit=10)

    assert [row["day"] for row in rows] == [
        f"2026-07-{session_id:02d}" for session_id in range(6, 16)
    ]
    assert mood.last_delta(7, "work", limit=10)["last_pre"] == 15


def test_state_rating_series_uses_latest_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE state_ratings(
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    for rating_id in range(1, 13):
        conn.execute(
            "INSERT INTO state_ratings(id,user_id,rating,created_at_utc) VALUES(?,?,?,?)",
            (rating_id, 9, rating_id, f"2026-07-{rating_id:02d}T08:00:00+00:00"),
        )
    conn.commit()
    monkeypatch.setattr(state_ratings, "db", _db_context(conn))

    rows = state_ratings.series(9, limit=10)

    assert [row["rating"] for row in rows] == list(range(3, 13))


def test_state_rating_day_filter_uses_local_timezone_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE state_ratings(
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO state_ratings(id,user_id,rating,created_at_utc) VALUES(?,?,?,?)",
        [
            (1, 11, 1, "2026-01-01T20:59:59+00:00"),
            (2, 11, 2, "2026-01-01T21:00:00+00:00"),
            (3, 11, 3, "2026-01-02T20:59:59+00:00"),
            (4, 11, 4, "2026-01-02T21:00:00+00:00"),
        ],
    )
    conn.commit()
    monkeypatch.setattr(state_ratings, "db", _db_context(conn))
    monkeypatch.setattr(state_ratings.settings, "TIMEZONE", "Europe/Moscow", raising=False)

    rows = state_ratings.series(11, day="2026-01-02", limit=20)

    assert [row["rating"] for row in rows] == [2, 3]


def test_body_area_streak_counts_adjacent_calendar_days_not_clicks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(support_ai.settings, "TIMEZONE", "Europe/Moscow", raising=False)
    observations = [
        BodyAreaObservation("Шея", "2026-07-04T10:00:00+00:00"),
        BodyAreaObservation("Шея", "2026-07-04T09:00:00+00:00"),
        BodyAreaObservation("Шея", "2026-07-03T10:00:00+00:00"),
        BodyAreaObservation("Шея", "2026-07-02T10:00:00+00:00"),
        BodyAreaObservation("Плечи", "2026-07-01T10:00:00+00:00"),
    ]

    area, days = support_ai._same_area_consecutive_days(observations)

    assert area == "Шея"
    assert days == 3


def test_body_area_streak_stops_at_missing_calendar_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(support_ai.settings, "TIMEZONE", "Europe/Moscow", raising=False)
    observations = [
        BodyAreaObservation("Шея", "2026-07-04T10:00:00+00:00"),
        BodyAreaObservation("Шея", "2026-07-02T10:00:00+00:00"),
    ]

    assert support_ai._same_area_consecutive_days(observations) == ("Шея", 1)


def test_start_payload_log_metadata_never_contains_paid_gift_token() -> None:
    token = "gift_0123456789abcdef0123456789abcdef"

    metadata = start._payload_log_meta(token)

    assert metadata["payload_kind"] == "paid_gift"
    assert metadata["payload_len"] == len(token)
    assert token not in repr(metadata)
    assert len(str(metadata["payload_sha256"])) == 16
