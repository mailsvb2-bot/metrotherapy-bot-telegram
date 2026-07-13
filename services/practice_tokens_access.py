from __future__ import annotations

"""Delivery-recovery facade over the DB-atomic reservation effect core."""

import sqlite3

from services.db import db
from services import practice_tokens_access_core as _core
from services.practice_tokens_access_core import *  # noqa: F403
from services.practice_tokens_wallet import (
    PracticeWallet,
    canonical_practice_user_id,
    ensure_schema,
    ensure_wallet,
    get_wallet_in_conn,
)

_existing_reserved = _core._existing_reserved
_reservation_row = _core._reservation_row


def _delivered_reservation_ids(user_id: int) -> list[str]:
    """Reservations with durable external-delivery evidence.

    A session-bound send normally proves delivery through mood_sessions.audio_sent.
    If the process dies after sending and writing account_audio_progress.pending_audio_no
    but before mood finalization, the matching account pending marker is also valid
    evidence. Unique active reservation/session+anchor indexes prevent an unrelated
    concurrent practice from being mistaken for that delivery.
    """

    with db() as conn:
        ensure_schema(conn)
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT r.reservation_id
                FROM practice_reservations r
                LEFT JOIN mood_sessions s ON s.id=r.session_id
                LEFT JOIN account_audio_progress ap
                  ON ap.account_id=r.user_id
                 AND ap.product_id='metrotherapy'
                 AND ap.program_id='full_series'
                 AND ap.pending_audio_no=r.audio_anchor
                WHERE r.user_id=?
                  AND r.status='reserved'
                  AND (
                        (r.session_id IS NOT NULL AND COALESCE(s.audio_sent,0)=1)
                     OR (r.audio_anchor IS NOT NULL AND ap.pending_audio_no IS NOT NULL)
                  )
                ORDER BY r.created_at, r.reservation_id
                """.strip(),
                (int(user_id),),
            ).fetchall()
        except sqlite3.Error as exc:
            error_text = str(exc).lower()
            if "does not exist" not in error_text and "no such table" not in error_text:
                raise
            rows = conn.execute(
                """
                SELECT DISTINCT r.reservation_id
                FROM practice_reservations r
                JOIN mood_sessions s ON s.id=r.session_id
                WHERE r.user_id=?
                  AND r.status='reserved'
                  AND COALESCE(s.audio_sent,0)=1
                ORDER BY r.created_at, r.reservation_id
                """.strip(),
                (int(user_id),),
            ).fetchall()
    return [str(row["reservation_id"]) for row in rows]


def reconcile_delivered_reservations(user_id: int) -> int:
    uid = canonical_practice_user_id(int(user_id))
    repaired = 0
    for reservation_id in _delivered_reservation_ids(uid):
        if _core.consume_reservation(
            reservation_id,
            reason="audio_delivery_reconciled",
        ):
            repaired += 1
    return repaired


def get_wallet(user_id: int) -> PracticeWallet:
    uid = canonical_practice_user_id(int(user_id))
    reconcile_delivered_reservations(uid)
    with db() as conn:
        ensure_wallet(conn, uid)
        return get_wallet_in_conn(conn, uid)


def has_paid_practice_access(user_id: int) -> bool:
    wallet = get_wallet(int(user_id))
    return int(wallet.available_tokens) > 0 or int(wallet.reserved_tokens) > 0
