from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from services import practice_tokens_access as access


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "services" / "practice_tokens_access.py"


def _connection(*, include_progress: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE practice_reservations(
            reservation_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            session_id INTEGER,
            audio_anchor INTEGER,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE mood_sessions(
            id INTEGER PRIMARY KEY,
            audio_sent INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    if include_progress:
        conn.execute(
            """
            CREATE TABLE account_audio_progress(
                account_id INTEGER NOT NULL,
                product_id TEXT NOT NULL,
                program_id TEXT NOT NULL,
                pending_audio_no INTEGER
            )
            """
        )
    return conn


def _patch_db(monkeypatch, conn: sqlite3.Connection) -> None:
    @contextmanager
    def fake_db():
        yield conn

    monkeypatch.setattr(access, "db", fake_db)
    monkeypatch.setattr(access, "ensure_schema", lambda _conn: None)


def _insert_reservations(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO mood_sessions(id, audio_sent) VALUES(?,?)",
        [(1, 1), (2, 1), (3, 0)],
    )
    conn.executemany(
        """
        INSERT INTO practice_reservations(
            reservation_id, user_id, session_id, audio_anchor, status, created_at
        ) VALUES(?,?,?,?,?,?)
        """,
        [
            ("r-late", 771, 2, 2, "reserved", "2026-01-02 00:00:00"),
            ("r-early", 771, 1, 1, "reserved", "2026-01-01 00:00:00"),
            ("r-ignore", 771, 3, 3, "reserved", "2025-12-31 00:00:00"),
        ],
    )


def test_delivery_recovery_deduplicates_join_evidence_and_orders_by_first_creation(monkeypatch) -> None:
    conn = _connection(include_progress=True)
    try:
        _insert_reservations(conn)
        conn.executemany(
            """
            INSERT INTO account_audio_progress(
                account_id, product_id, program_id, pending_audio_no
            ) VALUES(?,?,?,?)
            """,
            [
                (771, "metrotherapy", "full_series", 1),
                (771, "metrotherapy", "full_series", 1),
            ],
        )
        _patch_db(monkeypatch, conn)

        assert access._delivered_reservation_ids(771) == ["r-early", "r-late"]
    finally:
        conn.close()


def test_delivery_recovery_fallback_keeps_the_same_stable_order(monkeypatch) -> None:
    conn = _connection(include_progress=False)
    try:
        _insert_reservations(conn)
        _patch_db(monkeypatch, conn)

        assert access._delivered_reservation_ids(771) == ["r-early", "r-late"]
    finally:
        conn.close()


def test_delivery_recovery_sql_is_valid_for_postgres_distinct_ordering_rules() -> None:
    text = SOURCE.read_text(encoding="utf-8")

    assert "SELECT DISTINCT r.reservation_id" not in text
    assert text.count("MIN(r.created_at) AS first_created_at") == 2
    assert text.count("GROUP BY r.reservation_id") == 2
    assert text.count("ORDER BY first_created_at, r.reservation_id") == 2
