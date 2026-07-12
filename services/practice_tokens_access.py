from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from services.db import db, tx
from services.practice_tokens_wallet import (
    EMPTY_BALANCE_MESSAGE,
    RESERVE_FAILED_MESSAGE,
    PracticeAccessDecision,
    PracticeWallet,
    canonical_practice_user_id,
    enforcement_mode,
    ensure_schema,
    ensure_wallet,
    get_wallet_in_conn,
    insert_ledger,
    token_economy_enabled,
)


def _delivered_reservation_ids(user_id: int) -> list[str]:
    """Rows with durable evidence that audio already left the system."""

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
                     OR (r.session_id IS NULL AND r.audio_anchor IS NOT NULL AND ap.pending_audio_no IS NOT NULL)
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
        if consume_reservation(reservation_id, reason="audio_delivery_reconciled"):
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


def _existing_reserved(
    conn: Any,
    *,
    user_id: int,
    session_id: int | None,
    audio_anchor: int | None,
) -> Any | None:
    if session_id is not None:
        return conn.execute(
            """
            SELECT reservation_id FROM practice_reservations
            WHERE user_id=? AND session_id=? AND status='reserved'
            ORDER BY created_at, reservation_id LIMIT 1
            """.strip(),
            (int(user_id), int(session_id)),
        ).fetchone()
    if audio_anchor is not None:
        return conn.execute(
            """
            SELECT reservation_id FROM practice_reservations
            WHERE user_id=? AND session_id IS NULL AND audio_anchor=? AND status='reserved'
            ORDER BY created_at, reservation_id LIMIT 1
            """.strip(),
            (int(user_id), int(audio_anchor)),
        ).fetchone()
    return None


def reserve_practice(
    user_id: int,
    *,
    session_id: int | None = None,
    audio_anchor: int | None = None,
    reason: str = "audio_delivery",
) -> tuple[bool, PracticeWallet, str | None]:
    uid = canonical_practice_user_id(int(user_id))
    reservation_id = f"practice_res_{uuid.uuid4().hex}"
    with db() as conn:
        with tx(conn):
            ensure_wallet(conn, uid)
            existing = _existing_reserved(
                conn,
                user_id=uid,
                session_id=session_id,
                audio_anchor=audio_anchor,
            )
            if existing:
                return True, get_wallet_in_conn(conn, uid), str(existing["reservation_id"])

            insert_cursor = conn.execute(
                """
                INSERT OR IGNORE INTO practice_reservations(
                    reservation_id, user_id, amount, status, session_id, audio_anchor, reason
                ) VALUES(?,?,?,?,?,?,?)
                """.strip(),
                (
                    reservation_id,
                    uid,
                    1,
                    "reserved",
                    int(session_id) if session_id is not None else None,
                    int(audio_anchor) if audio_anchor is not None else None,
                    reason,
                ),
            )
            if int(getattr(insert_cursor, "rowcount", 0) or 0) <= 0:
                existing = _existing_reserved(
                    conn,
                    user_id=uid,
                    session_id=session_id,
                    audio_anchor=audio_anchor,
                )
                if existing:
                    return True, get_wallet_in_conn(conn, uid), str(existing["reservation_id"])
                return False, get_wallet_in_conn(conn, uid), None

            cursor = conn.execute(
                """
                UPDATE practice_wallets
                SET available_tokens=available_tokens - 1,
                    reserved_tokens=reserved_tokens + 1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND available_tokens > 0
                """.strip(),
                (uid,),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
                conn.execute(
                    "DELETE FROM practice_reservations WHERE reservation_id=? AND status='reserved'",
                    (reservation_id,),
                )
                return False, get_wallet_in_conn(conn, uid), None

            wallet_after = get_wallet_in_conn(conn, uid)
            insert_ledger(
                conn,
                user_id=uid,
                event_type="reserve",
                amount=-1,
                balance_after=int(wallet_after.available_tokens),
                reason=reason,
                idempotency_key=f"reserve:{reservation_id}",
            )
    return True, wallet_after, reservation_id


def _reservation_row(conn: Any, reservation_id: str) -> Any | None:
    return conn.execute(
        "SELECT * FROM practice_reservations WHERE reservation_id=?",
        (reservation_id,),
    ).fetchone()


def consume_reservation(reservation_id: str, *, reason: str = "audio_delivery_succeeded") -> bool:
    raw = str(reservation_id or "").strip()
    if not raw:
        return False
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            row = _reservation_row(conn, raw)
            if not row:
                return False
            status = str(row["status"])
            if status == "consumed":
                return True
            if status != "reserved":
                return False

            cursor = conn.execute(
                """
                UPDATE practice_reservations
                SET status='consumed', updated_at=CURRENT_TIMESTAMP
                WHERE reservation_id=? AND status='reserved'
                """.strip(),
                (raw,),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
                current = _reservation_row(conn, raw)
                return bool(current and str(current["status"]) == "consumed")

            user_id = int(row["user_id"])
            amount = int(row["amount"])
            conn.execute(
                """
                UPDATE practice_wallets
                SET reserved_tokens=CASE WHEN reserved_tokens >= ? THEN reserved_tokens - ? ELSE 0 END,
                    used_tokens=used_tokens + ?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE user_id=?
                """.strip(),
                (amount, amount, amount, user_id),
            )
            wallet_after = get_wallet_in_conn(conn, user_id)
            insert_ledger(
                conn,
                user_id=user_id,
                event_type="consume",
                amount=-amount,
                balance_after=int(wallet_after.available_tokens),
                reason=reason,
                idempotency_key=f"consume:{raw}",
            )
    return True


def release_reservation(reservation_id: str, *, reason: str = "audio_delivery_failed") -> bool:
    raw = str(reservation_id or "").strip()
    if not raw:
        return False
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            row = _reservation_row(conn, raw)
            if not row:
                return False
            status = str(row["status"])
            if status == "released":
                return True
            if status != "reserved":
                return False

            cursor = conn.execute(
                """
                UPDATE practice_reservations
                SET status='released', updated_at=CURRENT_TIMESTAMP
                WHERE reservation_id=? AND status='reserved'
                """.strip(),
                (raw,),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
                current = _reservation_row(conn, raw)
                return bool(current and str(current["status"]) == "released")

            user_id = int(row["user_id"])
            amount = int(row["amount"])
            conn.execute(
                """
                UPDATE practice_wallets
                SET available_tokens=available_tokens + ?,
                    reserved_tokens=CASE WHEN reserved_tokens >= ? THEN reserved_tokens - ? ELSE 0 END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE user_id=?
                """.strip(),
                (amount, amount, amount, user_id),
            )
            wallet_after = get_wallet_in_conn(conn, user_id)
            insert_ledger(
                conn,
                user_id=user_id,
                event_type="release",
                amount=amount,
                balance_after=int(wallet_after.available_tokens),
                reason=reason,
                idempotency_key=f"release:{raw}",
            )
    return True


def check_and_reserve_for_audio(
    user_id: int,
    *,
    is_demo: bool,
    session_id: int | None = None,
    audio_anchor: int | None = None,
) -> PracticeAccessDecision:
    mode = enforcement_mode()
    if is_demo or not token_economy_enabled() or mode == "off":
        return PracticeAccessDecision(True, mode, "free_demo_or_disabled")

    uid = canonical_practice_user_id(int(user_id))
    with db() as conn:
        ensure_wallet(conn, uid)
        existing = _existing_reserved(
            conn,
            user_id=uid,
            session_id=session_id,
            audio_anchor=audio_anchor,
        )
        if existing:
            return PracticeAccessDecision(
                True,
                mode,
                "existing_reservation",
                reservation_id=str(existing["reservation_id"]),
            )
        wallet = get_wallet_in_conn(conn, uid)

    if wallet.available_tokens <= 0:
        if mode == "soft":
            return PracticeAccessDecision(
                True,
                mode,
                "soft_insufficient_balance",
                warning=EMPTY_BALANCE_MESSAGE,
            )
        return PracticeAccessDecision(
            False,
            mode,
            "insufficient_balance",
            message=EMPTY_BALANCE_MESSAGE,
        )

    ok, _wallet_after, reservation_id = reserve_practice(
        uid,
        session_id=session_id,
        audio_anchor=audio_anchor,
    )
    if not ok:
        if mode == "soft":
            return PracticeAccessDecision(
                True,
                mode,
                "soft_reserve_failed",
                warning=RESERVE_FAILED_MESSAGE,
            )
        return PracticeAccessDecision(
            False,
            mode,
            "reserve_failed",
            message=RESERVE_FAILED_MESSAGE,
        )
    return PracticeAccessDecision(True, mode, "reserved", reservation_id=reservation_id)


def finalize_audio_access(decision: PracticeAccessDecision, *, delivered: bool) -> bool:
    if not decision.reservation_id:
        return True
    if delivered:
        return consume_reservation(decision.reservation_id)
    return release_reservation(decision.reservation_id)
