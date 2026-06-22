from __future__ import annotations
import logging


from datetime import datetime, timezone, timedelta
from core.time_utils import utc_now

from services.db import get_db
from services.subscription import is_active as is_sub_active
def get_user_segment(user_id: int) -> str:
    """Детерминированная сегментация для автоворонок/аналитики.

    Сегменты (минимальный стабильный набор):
      - active_sub: есть активная подписка
      - expired_sub: подписка была, но закончилась (за последние 60 дней)
      - demo_only: есть демо-активность, но нет оплат
      - ref_joined: пришёл по реф-метке (есть запись referrals)
      - silent: давно не было активности (state-log 14+ дней)
      - new: всё остальное
    """
    uid = int(user_id)
    if is_sub_active(uid):
        return "active_sub"

    now = utc_now().replace(microsecond=0)

    with get_db() as conn:
        # была ли подписка и закончилась ли она недавно (по касаниям)
        row = conn.execute(
            "SELECT COALESCE(status,'active') AS status, paid_at, started_at FROM subscriptions WHERE user_id=? LIMIT 1",
            (uid,),
        ).fetchone()
        if row and ("status" in row.keys()) and row["status"] == "finished":
            # paid_at используем как "последний значимый момент" (покупка/продление)
            ts = row["paid_at"] or row["started_at"]
            if ts:
                try:
                    t = datetime.fromisoformat(str(ts))
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    if t >= (now - timedelta(days=60)):
                        return "expired_sub"
                except (ValueError, TypeError):
                    logging.getLogger(__name__).exception("Bad ISO timestamp in subscriptions")

        # реферал
        row = conn.execute(
            "SELECT 1 FROM referrals WHERE referred_id=? LIMIT 1",
            (uid,),
        ).fetchone()
        if row:
            return "ref_joined"

        # демо активность
        demo = conn.execute(
            "SELECT 1 FROM demo_events WHERE user_id=? LIMIT 1",
            (uid,),
        ).fetchone()
        paid = conn.execute(
            "SELECT 1 FROM payments WHERE user_id=? LIMIT 1",
            (uid,),
        ).fetchone()
        if demo and not paid:
            return "demo_only"

        # тишина по state-log
        st = conn.execute(
            "SELECT MAX(ts) AS mx FROM user_state_log WHERE user_id=?",
            (uid,),
        ).fetchone()
        if st and ("mx" in st.keys()) and st["mx"]:
            try:
                last = datetime.fromisoformat(str(st["mx"]))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if last <= (now - timedelta(days=14)):
                    return "silent"
            except (ValueError, TypeError):
                logging.getLogger(__name__).exception("Bad ISO timestamp in user_state_log")

    return "new"


def segment_counts(limit_users: int = 5000) -> dict[str, int]:
    """Считает сегменты по известным пользователям.

    Не меняет UX, используется в админке.
    """
    counts: dict[str, int] = {}
    with get_db() as conn:
        # В разных версиях проекта таблица users не всегда имеет колонку id.
        # Контракт везде один: ключ пользователя хранится как users.user_id.
        rows = conn.execute(
            "SELECT user_id FROM users ORDER BY user_id DESC LIMIT ?",
            (int(limit_users),),
        ).fetchall()
    for r in rows:
        seg = get_user_segment(int(r["user_id"]))
        counts[seg] = counts.get(seg, 0) + 1
    return counts
