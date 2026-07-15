from __future__ import annotations

from typing import Any

_REQUIRED = frozenset({"practice_token_lots", "practice_reservation_lots"})


def ensure_lot_schema(conn: Any) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    existing = {str(row["name"] if hasattr(row, "keys") else row[0]) for row in rows}
    missing = sorted(_REQUIRED - existing)
    if missing:
        raise RuntimeError(f"practice_token_lot_schema_not_migrated:{','.join(missing)}")


def create_lot_in_conn(
    conn: Any,
    *,
    lot_key: str,
    user_id: int,
    provider: str,
    provider_payment_id: str,
    package_id: str,
    amount: int,
    refundable: bool,
) -> int:
    ensure_lot_schema(conn)
    tokens = int(amount)
    if tokens <= 0:
        raise ValueError("practice_lot_amount_must_be_positive")
    conn.execute(
        """
        INSERT OR IGNORE INTO practice_token_lots(
            lot_key, user_id, provider, provider_payment_id, package_id,
            granted_tokens, available_tokens, refundable
        ) VALUES(?,?,?,?,?,?,?,?)
        """.strip(),
        (
            str(lot_key), int(user_id), str(provider or ""),
            str(provider_payment_id or ""), str(package_id or ""),
            tokens, tokens, 1 if refundable else 0,
        ),
    )
    row = conn.execute(
        "SELECT id, user_id, granted_tokens FROM practice_token_lots WHERE lot_key=? LIMIT 1",
        (str(lot_key),),
    ).fetchone()
    if row is None or int(row["user_id"]) != int(user_id) or int(row["granted_tokens"]) != tokens:
        raise RuntimeError("practice_lot_idempotency_conflict")
    return int(row["id"])


def reserve_from_lots(conn: Any, *, user_id: int, reservation_id: str, amount: int) -> None:
    ensure_lot_schema(conn)
    remaining = int(amount)
    if remaining <= 0:
        raise ValueError("practice_reservation_amount_invalid")
    rows = conn.execute(
        """
        SELECT id, available_tokens
        FROM practice_token_lots
        WHERE user_id=? AND available_tokens > 0
        ORDER BY id ASC
        """.strip(),
        (int(user_id),),
    ).fetchall()
    allocations: list[tuple[int, int]] = []
    for row in rows:
        take = min(remaining, int(row["available_tokens"]))
        if take <= 0:
            continue
        allocations.append((int(row["id"]), take))
        remaining -= take
        if remaining == 0:
            break
    if remaining:
        raise RuntimeError("practice_lot_balance_mismatch")
    for lot_id, take in allocations:
        cursor = conn.execute(
            """
            UPDATE practice_token_lots
            SET available_tokens=available_tokens-?, reserved_tokens=reserved_tokens+?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND available_tokens>=?
            """.strip(),
            (take, take, lot_id, take),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
            raise RuntimeError("practice_lot_reserve_race")
        conn.execute(
            """
            INSERT INTO practice_reservation_lots(reservation_id, lot_id, amount, status)
            VALUES(?,?,?,'reserved')
            """.strip(),
            (str(reservation_id), lot_id, take),
        )


def consume_lot_reservation(conn: Any, reservation_id: str) -> None:
    ensure_lot_schema(conn)
    rows = conn.execute(
        "SELECT lot_id, amount FROM practice_reservation_lots WHERE reservation_id=? AND status='reserved'",
        (str(reservation_id),),
    ).fetchall()
    if not rows:
        raise RuntimeError("practice_reservation_lot_missing")
    for row in rows:
        lot_id, amount = int(row["lot_id"]), int(row["amount"])
        cursor = conn.execute(
            """
            UPDATE practice_token_lots
            SET reserved_tokens=reserved_tokens-?, used_tokens=used_tokens+?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND reserved_tokens>=?
            """.strip(),
            (amount, amount, lot_id, amount),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
            raise RuntimeError("practice_lot_consume_mismatch")
    conn.execute(
        "UPDATE practice_reservation_lots SET status='consumed', updated_at=CURRENT_TIMESTAMP WHERE reservation_id=? AND status='reserved'",
        (str(reservation_id),),
    )


def release_lot_reservation(conn: Any, reservation_id: str) -> None:
    ensure_lot_schema(conn)
    rows = conn.execute(
        "SELECT lot_id, amount FROM practice_reservation_lots WHERE reservation_id=? AND status='reserved'",
        (str(reservation_id),),
    ).fetchall()
    if not rows:
        raise RuntimeError("practice_reservation_lot_missing")
    for row in rows:
        lot_id, amount = int(row["lot_id"]), int(row["amount"])
        cursor = conn.execute(
            """
            UPDATE practice_token_lots
            SET reserved_tokens=reserved_tokens-?, available_tokens=available_tokens+?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND reserved_tokens>=?
            """.strip(),
            (amount, amount, lot_id, amount),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
            raise RuntimeError("practice_lot_release_mismatch")
    conn.execute(
        "UPDATE practice_reservation_lots SET status='released', updated_at=CURRENT_TIMESTAMP WHERE reservation_id=? AND status='reserved'",
        (str(reservation_id),),
    )


def payment_lot(conn: Any, *, provider: str, provider_payment_id: str) -> dict[str, Any]:
    ensure_lot_schema(conn)
    row = conn.execute(
        """
        SELECT id, user_id, package_id, granted_tokens, available_tokens,
               reserved_tokens, used_tokens, refund_held_tokens, refunded_tokens, refundable
        FROM practice_token_lots
        WHERE provider=? AND provider_payment_id=?
        LIMIT 1
        """.strip(),
        (str(provider), str(provider_payment_id)),
    ).fetchone()
    return {str(key): row[key] for key in row.keys()} if row is not None else {}


def hold_payment_lot_for_refund(conn: Any, *, provider: str, provider_payment_id: str, amount: int) -> None:
    cursor = conn.execute(
        """
        UPDATE practice_token_lots
        SET available_tokens=available_tokens-?, refund_held_tokens=refund_held_tokens+?, updated_at=CURRENT_TIMESTAMP
        WHERE provider=? AND provider_payment_id=? AND refundable=1
          AND granted_tokens=? AND available_tokens=? AND reserved_tokens=0
          AND used_tokens=0 AND refund_held_tokens=0 AND refunded_tokens=0
        """.strip(),
        (int(amount), int(amount), str(provider), str(provider_payment_id), int(amount), int(amount)),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
        raise RuntimeError("payment_token_lot_not_fully_refundable")


def release_payment_lot_refund_hold(conn: Any, *, provider: str, provider_payment_id: str, amount: int) -> None:
    cursor = conn.execute(
        """
        UPDATE practice_token_lots
        SET refund_held_tokens=refund_held_tokens-?, available_tokens=available_tokens+?, updated_at=CURRENT_TIMESTAMP
        WHERE provider=? AND provider_payment_id=? AND refund_held_tokens=?
        """.strip(),
        (int(amount), int(amount), str(provider), str(provider_payment_id), int(amount)),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
        raise RuntimeError("payment_token_refund_hold_missing")


def finalize_payment_lot_refund(conn: Any, *, provider: str, provider_payment_id: str, amount: int) -> None:
    cursor = conn.execute(
        """
        UPDATE practice_token_lots
        SET refund_held_tokens=refund_held_tokens-?, refunded_tokens=refunded_tokens+?, updated_at=CURRENT_TIMESTAMP
        WHERE provider=? AND provider_payment_id=? AND refund_held_tokens=?
        """.strip(),
        (int(amount), int(amount), str(provider), str(provider_payment_id), int(amount)),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
        raise RuntimeError("payment_token_refund_hold_missing")
