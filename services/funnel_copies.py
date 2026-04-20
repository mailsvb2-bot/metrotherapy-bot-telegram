from __future__ import annotations


from datetime import datetime, timezone

from services.db import db


def get_active_copy(key: str, variant: str) -> str | None:
    """Возвращает активный кастомный текст воронки, если есть.

    key: строковый ключ шага (например: "offer", "offer_nextday", "nudge")
    variant: "A"/"B" или "-" (для не-A/B шагов)
    """
    key = str(key).strip()
    variant = str(variant).strip().upper()
    with db() as conn:
        row = conn.execute(
            """
            SELECT text FROM funnel_copies
            WHERE key=? AND variant=? AND is_active=1
            ORDER BY id DESC LIMIT 1
            """,
            (key, variant),
        ).fetchone()
    return str(row["text"]) if row else None


def upsert_copy(key: str, variant: str, text: str, created_by: int | None = None) -> None:
    key = str(key).strip()
    variant = str(variant).strip().upper()
    text = str(text).strip()
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        # Делаем просто INSERT (а не UPDATE) чтобы сохранялась история; активным будет последний.
        conn.execute(
            """
            INSERT INTO funnel_copies(key, variant, text, created_by, created_at, is_active)
            VALUES(?,?,?,?,?,1)
            """,
            (key, variant, text, int(created_by) if created_by is not None else None, now),
        )
        conn.commit()


def deactivate_key(key: str) -> None:
    key = str(key).strip()
    with db() as conn:
        conn.execute("UPDATE funnel_copies SET is_active=0 WHERE key=?", (key,))
        conn.commit()


def list_latest(key: str, limit: int = 10) -> list[dict]:
    key = str(key).strip()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, key, variant, substr(text,1,200) AS preview, created_by, created_at, is_active
            FROM funnel_copies
            WHERE key=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (key, int(limit)),
        ).fetchall()
    return [dict(r) for r in (rows or [])]
