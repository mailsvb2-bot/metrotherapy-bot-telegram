from __future__ import annotations

from datetime import datetime
from core.time_utils import utc_now
import secrets
import sqlite3
from services.db import db
from services.plans import get_plan_by_id




def _gift_payload(row) -> dict:
    plan_id = row.get('plan_id')
    try:
        pid = int(plan_id) if plan_id is not None else None
    except (TypeError, ValueError):
        pid = None
    return {
        "plan_id": pid,
        "scope": row.get("scope"),
        "days": int(row.get("days") or 0),
        "created_by": int(row.get("created_by") or 0),
        "recipient_id": row.get("recipient_id"),
        "status": (row.get("status") or "created").strip(),
    }


def create_gift(plan_id: int, created_by: int, recipient_id: int | None = None) -> str:
    """Создать подарочный код (ещё не оплачен).

    В БД хранится plan_id как источник истины.
    scope/days оставлены для совместимости, но считаются производными.
    """
    plan = get_plan_by_id(int(plan_id))
    if not plan:
        raise ValueError(f"unknown plan_id={plan_id}")
    scope = str(plan.get("scope") or "")
    days = int(plan.get("days") or 0)
    if not scope or days <= 0:
        raise ValueError(f"invalid plan: {plan}")

    for _ in range(8):
        code = secrets.token_urlsafe(10).replace("-", "").replace("_", "")
        try:
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO gift_codes(code, plan_id, scope, days, created_by, recipient_id, created_at, paid, status)
                    VALUES(?,?,?,?,?,?,?,?, 'created')
                    """,
                    (
                        code,
                        int(plan_id),
                        scope,
                        days,
                        int(created_by),
                        int(recipient_id) if recipient_id is not None else None,
                        utc_now().replace(microsecond=0).isoformat(),
                        0,
                    ),
                )
            return code
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("failed to generate unique gift code")

def mark_gift_paid(code: str, payment_id: str | None = None) -> bool:
    """Mark gift as paid once. Returns True if changed, False if already paid."""
    with db() as conn:
        return mark_gift_paid_tx(conn, code, payment_id=payment_id)


def mark_gift_paid_tx(conn, code: str, payment_id: str | None = None) -> bool:
    """Транзакционная версия mark_gift_paid()."""
    cur = conn.execute(
        "UPDATE gift_codes SET paid=1, paid_payment_id=COALESCE(paid_payment_id, ?), "
        "status=CASE WHEN status IS NULL THEN 'created' ELSE status END "
        "WHERE code=? AND paid!=1",
        ((payment_id or None), code),
    )
    return getattr(cur, 'rowcount', 0) == 1



def get_gift_status(code: str) -> tuple[bool, str, dict | None]:
    """Return gift code status without redeeming."""
    with db() as conn:
        row = conn.execute("SELECT * FROM gift_codes WHERE code=?", (code,)).fetchone()
        if not row:
            return False, "❌ Подарочный код не найден.", None
        if row.get("expires_at") and str(row["expires_at"]).strip():
            # best-effort TTL support
            try:
                exp = str(row["expires_at"]).strip()
                # compare as strings (ISO) is ok in UTC format
                if exp and utc_now().replace(microsecond=0).isoformat() > exp:
                    return False, "⛔️ Срок действия подарка истёк.", None
            except (TypeError, ValueError, AttributeError):
                # TTL is optional; never break gift flow because of a bad timestamp
                log = __import__("logging").getLogger(__name__)
                log.warning("Bad expires_at value for gift code %s", code)

        if int(row["paid"] or 0) != 1:
            return False, "⛔️ Этот подарок ещё не оплачен.", None

        status = (row.get("status") or "created").strip()
        if status == "activated" or row.get("redeemed_by") is not None:
            return False, "⛔️ Этот подарок уже активирован.", _gift_payload(row)

        return True, "OK", _gift_payload(row)


def redeem_gift(code: str, user_id: int) -> tuple[bool, str, dict | None]:
    """Claim gift (created->claimed) idempotently.

    Activation (granting access) should be done separately and then mark as activated.
    """
    uid = int(user_id)
    with db() as conn:
        row = conn.execute("SELECT * FROM gift_codes WHERE code=?", (code,)).fetchone()
        if not row:
            return False, "❌ Подарочный код не найден.", None
        if int(row["paid"] or 0) != 1:
            return False, "⛔️ Этот подарок ещё не оплачен.", None

        # recipient lock (optional)
        rid = row.get("recipient_id")
        if rid is not None and int(rid) != uid:
            return False, "⛔️ Этот подарок предназначен другому пользователю.", None

        status = (row.get("status") or "created").strip()
        if status == "activated" or row.get("redeemed_by") is not None:
            return False, "⛔️ Этот подарок уже активирован.", _gift_payload(row)

        # idempotent claim
        if status == "claimed" and row.get("claimed_by") is not None and int(row["claimed_by"]) == uid:
            return True, "✅ Подарок уже принят.", _gift_payload(row)

        cur = conn.execute(
            "UPDATE gift_codes SET status='claimed', claimed_by=?, claimed_at=? "
            "WHERE code=? AND paid=1 AND (status IS NULL OR status='created') AND (claimed_by IS NULL)",
            (uid, utc_now().replace(microsecond=0).isoformat(), code),
        )
        if getattr(cur, "rowcount", 0) != 1:
            # someone else claimed concurrently
            row2 = conn.execute("SELECT * FROM gift_codes WHERE code=?", (code,)).fetchone()
            if row2 and row2.get("claimed_by") is not None and int(row2["claimed_by"]) == uid:
                return True, "✅ Подарок уже принят.", _gift_payload(row2)
            return False, "⛔️ Этот подарок уже принят другим пользователем.", None

        row3 = conn.execute("SELECT * FROM gift_codes WHERE code=?", (code,)).fetchone()
        return True, "✅ Подарок принят.", _gift_payload(row3)


def activate_gift(code: str, user_id: int) -> bool:
    """Mark claimed gift as activated (claimed->activated) idempotently."""
    uid = int(user_id)
    with db() as conn:
        row = conn.execute("SELECT * FROM gift_codes WHERE code=?", (code,)).fetchone()
        if not row:
            return False
        if int(row.get('paid') or 0) != 1:
            return False
        if (row.get('claimed_by') is not None) and int(row['claimed_by']) != uid:
            return False
        cur = conn.execute(
            "UPDATE gift_codes SET status='activated', activated_at=?, redeemed_by=COALESCE(redeemed_by, ?), redeemed_at=COALESCE(redeemed_at, ?) "
            "WHERE code=? AND paid=1 AND (status='claimed' OR status IS NULL) AND (claimed_by=? OR claimed_by IS NULL)",
            (utc_now().replace(microsecond=0).isoformat(), uid, utc_now().replace(microsecond=0).isoformat(), code, uid),
        )
        return getattr(cur, 'rowcount', 0) == 1
