from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from services.db import db


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass(frozen=True)
class Subscription:
    user_id: int
    plan_type: str | None
    total_morning: int
    total_evening: int
    used_morning: int
    used_evening: int
    status: str | None
    started_at: str | None
    scope: str | None

    @property
    def remaining_morning(self) -> int:
        return max(0, int(self.total_morning) - int(self.used_morning))

    @property
    def remaining_evening(self) -> int:
        return max(0, int(self.total_evening) - int(self.used_evening))

    @property
    def is_finished(self) -> bool:
        return self.remaining_morning <= 0 and self.remaining_evening <= 0


def get_subscription(user_id: int) -> Optional[Subscription]:
    with db() as conn:
        r = conn.execute(
            """
            SELECT user_id,
                   plan_type,
                   COALESCE(total_morning,0) AS total_morning,
                   COALESCE(total_evening,0) AS total_evening,
                   COALESCE(used_morning,0)  AS used_morning,
                   COALESCE(used_evening,0)  AS used_evening,
                   COALESCE(status,'active') AS status,
                   started_at,
                   scope
            FROM subscriptions
            WHERE user_id=?
            """,
            (int(user_id),),
        ).fetchone()
    if not r:
        return None
    return Subscription(
        user_id=int(r[0]),
        plan_type=(r[1] or None),
        total_morning=int(r[2] or 0),
        total_evening=int(r[3] or 0),
        used_morning=int(r[4] or 0),
        used_evening=int(r[5] or 0),
        status=(r[6] or None),
        started_at=(r[7] or None),
        scope=(r[8] or None),
    )


def is_active(user_id: int) -> bool:
    """Совместимость со старым кодом: активна ли подписка вообще (по касаниям)."""
    s = get_subscription(user_id)
    if not s:
        return False
    if (s.status or "active") != "active":
        return False
    return not s.is_finished


def get_scope(user_id: int) -> str | None:
    """Возвращает scope активной подписки: morning | evening | both, или None.

    Нужен для обратной совместимости (services.store и диагностические сообщения).
    """

    s = get_subscription(user_id)
    if not s:
        return None
    if (s.status or "active") != "active" or s.is_finished:
        return None

    # Если scope уже сохранён — используем его.
    if s.scope in ("morning", "evening", "both"):
        return s.scope

    # Fallback по totals.
    if (s.total_morning or 0) > 0 and (s.total_evening or 0) > 0:
        return "both"
    if (s.total_morning or 0) > 0:
        return "morning"
    if (s.total_evening or 0) > 0:
        return "evening"
    return None


def has_access(user_id: int, slot: str) -> bool:
    """slot: 'morning' | 'evening'. Доступ = есть активная подписка и есть оставшиеся касания."""
    s = get_subscription(user_id)
    if not s:
        return False
    if (s.status or "active") != "active":
        return False
    if slot == "morning":
        return s.remaining_morning > 0
    if slot == "evening":
        return s.remaining_evening > 0
    return False


def remaining(user_id: int) -> Tuple[int, int]:
    s = get_subscription(user_id)
    if not s:
        return (0, 0)
    return (s.remaining_morning, s.remaining_evening)


def grant(user_id: int, scope: str, days: int, *, price: int = 0, source: str = "pay", gift_id: str | None = None) -> None:
    """
    Выдача подписки как фиксированного количества рабочих касаний.
    days здесь = количество касаний (обычно 5 или 20).
    scope: morning | evening | both
    """
    with db() as conn:
        grant_tx(conn, user_id, scope, days, price=price, source=source, gift_id=gift_id)


def grant_tx(
    conn,
    user_id: int,
    scope: str,
    days: int,
    *,
    price: int = 0,
    source: str = "pay",
    gift_id: str | None = None,
) -> None:
    """Транзакционная версия grant().

    Можно вызывать внутри внешней транзакции (payments/gifts), чтобы выдача доступа
    и запись состояния в subscriptions были атомарны.
    """
    days = int(days)
    add_m = days if scope in ("morning", "both") else 0
    add_e = days if scope in ("evening", "both") else 0
    now = _now_utc().isoformat()

    row = conn.execute(
        """
        SELECT COALESCE(total_morning,0), COALESCE(total_evening,0),
               COALESCE(used_morning,0),  COALESCE(used_evening,0),
               COALESCE(status,'active')
        FROM subscriptions WHERE user_id=?
        """,
        (int(user_id),),
    ).fetchone()

    if not row:
        conn.execute(
            """
            INSERT INTO subscriptions(
                user_id, plan_type,
                total_morning, total_evening,
                used_morning, used_evening,
                status, started_at, scope,
                created_at, paid_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(user_id),
                scope,
                int(add_m),
                int(add_e),
                0,
                0,
                "active",
                now,
                scope,
                now,
                now,
            ),
        )
        return

    total_m, total_e, used_m, used_e, status = row
    total_m = int(total_m or 0) + int(add_m)
    total_e = int(total_e or 0) + int(add_e)

    if (status or "active") != "active":
        used_m, used_e = 0, 0
        conn.execute(
            """
            UPDATE subscriptions
               SET plan_type=?,
                   total_morning=?, total_evening=?,
                   used_morning=?, used_evening=?,
                   status='active',
                   started_at=?,
                   scope=?,
                   paid_at=?
             WHERE user_id=?
            """,
            (scope, total_m, total_e, int(used_m), int(used_e), now, scope, now, int(user_id)),
        )
        return

    conn.execute(
        """
        UPDATE subscriptions
           SET plan_type=?,
               total_morning=?,
               total_evening=?,
               scope=?,
               paid_at=?
         WHERE user_id=?
        """,
        (scope, total_m, total_e, scope, now, int(user_id)),
    )


def register_touch(user_id: int, slot: str) -> bool:
    """
    Засчитываем касание СТРОГО ПОСЛЕ факта (аудио реально отправлено).
    Возвращает True, если касание засчитано (и было что списывать).
    """
    if slot not in ("morning", "evening"):
        return False

    with db() as conn:
        if slot == "morning":
            cur = conn.execute(
                """
                UPDATE subscriptions
                   SET used_morning = COALESCE(used_morning,0) + 1
                 WHERE user_id=?
                   AND COALESCE(status,'active')='active'
                   AND COALESCE(used_morning,0) < COALESCE(total_morning,0)
                """,
                (int(user_id),),
            )
        else:
            cur = conn.execute(
                """
                UPDATE subscriptions
                   SET used_evening = COALESCE(used_evening,0) + 1
                 WHERE user_id=?
                   AND COALESCE(status,'active')='active'
                   AND COALESCE(used_evening,0) < COALESCE(total_evening,0)
                """,
                (int(user_id),),
            )

        changed = int(getattr(cur, "rowcount", 0) or 0)
        if changed <= 0:
            return False

        # если лимиты исчерпаны по обоим слотам — помечаем finished
        r = conn.execute(
            """
            SELECT COALESCE(total_morning,0), COALESCE(total_evening,0),
                   COALESCE(used_morning,0),  COALESCE(used_evening,0)
              FROM subscriptions
             WHERE user_id=?
            """,
            (int(user_id),),
        ).fetchone()
        if not r:
            return True
        total_m, total_e, used_m, used_e = map(int, r)
        if used_m >= total_m and used_e >= total_e:
            conn.execute("UPDATE subscriptions SET status='finished' WHERE user_id=?", (int(user_id),))
        return True
