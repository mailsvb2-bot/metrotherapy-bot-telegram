from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "practice_journey_consistency_v3"


def _dedupe_reserved_sessions(conn: sqlite3.Connection) -> set[int]:
    affected_users: set[int] = set()

    session_groups = conn.execute(
        """
        SELECT user_id, session_id
        FROM practice_reservations
        WHERE status='reserved' AND session_id IS NOT NULL
        GROUP BY user_id, session_id
        HAVING COUNT(*) > 1
        """.strip()
    ).fetchall()
    for group in session_groups:
        user_id = int(group["user_id"])
        session_id = int(group["session_id"])
        rows = conn.execute(
            """
            SELECT reservation_id
            FROM practice_reservations
            WHERE user_id=? AND session_id=? AND status='reserved'
            ORDER BY created_at, reservation_id
            """.strip(),
            (user_id, session_id),
        ).fetchall()
        for row in rows[1:]:
            conn.execute(
                """
                UPDATE practice_reservations
                SET status='released', updated_at=CURRENT_TIMESTAMP
                WHERE reservation_id=? AND status='reserved'
                """.strip(),
                (str(row["reservation_id"]),),
            )
        affected_users.add(user_id)

    anchor_groups = conn.execute(
        """
        SELECT user_id, audio_anchor
        FROM practice_reservations
        WHERE status='reserved' AND session_id IS NULL AND audio_anchor IS NOT NULL
        GROUP BY user_id, audio_anchor
        HAVING COUNT(*) > 1
        """.strip()
    ).fetchall()
    for group in anchor_groups:
        user_id = int(group["user_id"])
        audio_anchor = int(group["audio_anchor"])
        rows = conn.execute(
            """
            SELECT reservation_id
            FROM practice_reservations
            WHERE user_id=? AND session_id IS NULL AND audio_anchor=? AND status='reserved'
            ORDER BY created_at, reservation_id
            """.strip(),
            (user_id, audio_anchor),
        ).fetchall()
        for row in rows[1:]:
            conn.execute(
                """
                UPDATE practice_reservations
                SET status='released', updated_at=CURRENT_TIMESTAMP
                WHERE reservation_id=? AND status='reserved'
                """.strip(),
                (str(row["reservation_id"]),),
            )
        affected_users.add(user_id)

    return affected_users


def _dedupe_pending_paid_sessions(conn: sqlite3.Connection) -> set[int]:
    affected_users: set[int] = set()
    groups = conn.execute(
        """
        SELECT user_id, anchor_id
        FROM mood_sessions
        WHERE anchor_id IS NOT NULL
          AND COALESCE(audio_sent,0)=0
          AND source IN ('auto','settings')
        GROUP BY user_id, anchor_id
        HAVING COUNT(*) > 1
        """.strip()
    ).fetchall()

    for group in groups:
        user_id = int(group["user_id"])
        anchor_id = int(group["anchor_id"])
        rows = conn.execute(
            """
            SELECT id
            FROM mood_sessions
            WHERE user_id=? AND anchor_id=?
              AND COALESCE(audio_sent,0)=0
              AND source IN ('auto','settings')
            ORDER BY id DESC
            """.strip(),
            (user_id, anchor_id),
        ).fetchall()
        keep_id = int(rows[0]["id"])
        drop_ids = [int(row["id"]) for row in rows[1:]]
        for session_id in drop_ids:
            conn.execute(
                """
                UPDATE practice_reservations
                SET status='released', updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND session_id=? AND status='reserved'
                """.strip(),
                (user_id, session_id),
            )
            conn.execute("DELETE FROM mood_sessions WHERE id=?", (session_id,))
        if drop_ids:
            affected_users.add(user_id)
        conn.execute(
            "DELETE FROM mood_sessions WHERE user_id=? AND anchor_id=? AND COALESCE(audio_sent,0)=0 "
            "AND source IN ('auto','settings') AND id<>?",
            (user_id, anchor_id, keep_id),
        )

    return affected_users


def _rebuild_wallet(conn: sqlite3.Connection, user_id: int) -> None:
    wallet = conn.execute(
        """
        SELECT available_tokens, reserved_tokens, used_tokens
        FROM practice_wallets
        WHERE user_id=?
        """.strip(),
        (int(user_id),),
    ).fetchone()
    if wallet is None:
        return

    total_before = (
        int(wallet["available_tokens"] or 0)
        + int(wallet["reserved_tokens"] or 0)
        + int(wallet["used_tokens"] or 0)
    )
    reserved = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS n
        FROM practice_reservations
        WHERE user_id=? AND status='reserved'
        """.strip(),
        (int(user_id),),
    ).fetchone()
    total_reserved = int(reserved["n"] if reserved else 0)
    total_used = int(wallet["used_tokens"] or 0)
    available = max(0, total_before - total_used - total_reserved)

    conn.execute(
        """
        UPDATE practice_wallets
        SET available_tokens=?,
            reserved_tokens=?,
            used_tokens=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE user_id=?
        """.strip(),
        (available, total_reserved, total_used, int(user_id)),
    )


def _dedupe_demo_events(conn: sqlite3.Connection) -> None:
    groups = conn.execute(
        """
        SELECT user_id, kind, message_id
        FROM demo_events
        GROUP BY user_id, kind, message_id
        HAVING COUNT(*) > 1
        """.strip()
    ).fetchall()
    for group in groups:
        user_id = int(group["user_id"])
        kind = str(group["kind"])
        message_id = int(group["message_id"])
        rows = conn.execute(
            """
            SELECT id, ack_at_utc, ack_delay_sec
            FROM demo_events
            WHERE user_id=? AND kind=? AND message_id=?
            ORDER BY id
            """.strip(),
            (user_id, kind, message_id),
        ).fetchall()
        keep_id = int(rows[0]["id"])
        ack_values = [str(row["ack_at_utc"]) for row in rows if row["ack_at_utc"]]
        delay_values = [int(row["ack_delay_sec"]) for row in rows if row["ack_delay_sec"] is not None]
        conn.execute(
            "UPDATE demo_events SET ack_at_utc=?, ack_delay_sec=? WHERE id=?",
            (
                max(ack_values) if ack_values else None,
                max(delay_values) if delay_values else None,
                keep_id,
            ),
        )
        for row in rows[1:]:
            conn.execute("DELETE FROM demo_events WHERE id=?", (int(row["id"]),))


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)

    affected_users = _dedupe_reserved_sessions(conn)
    affected_users.update(_dedupe_pending_paid_sessions(conn))
    for user_id in sorted(affected_users):
        _rebuild_wallet(conn, user_id)

    _dedupe_demo_events(conn)

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_practice_reservation_active_session
        ON practice_reservations(user_id, session_id)
        WHERE status='reserved' AND session_id IS NOT NULL
        """.strip()
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_practice_reservation_active_anchor
        ON practice_reservations(user_id, audio_anchor)
        WHERE status='reserved' AND session_id IS NULL AND audio_anchor IS NOT NULL
        """.strip()
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_mood_pending_paid_anchor
        ON mood_sessions(user_id, anchor_id)
        WHERE anchor_id IS NOT NULL
          AND audio_sent=0
          AND source IN ('auto','settings')
        """.strip()
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_demo_events_user_kind_message
        ON demo_events(user_id, kind, message_id)
        """.strip()
    )

    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
