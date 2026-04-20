from __future__ import annotations
import logging


import json
from datetime import datetime, timezone, timedelta
from core.time_utils import utc_now

from services.db import get_db
from services.subscription import is_active as is_sub_active
from services.events import log_event


SC_DEMO_NOPAY_24H = "demo_nopay_24h"
SC_EXPIRED_RETURN_3D = "expired_return_3d"
def already_sent(user_id: int, scenario_key: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM funnel_events WHERE user_id=? AND scenario_key=? LIMIT 1",
            (int(user_id), str(scenario_key)),
        ).fetchone()
    return bool(row)


def mark_sent(user_id: int, scenario_key: str, meta: dict | None = None) -> bool:
    """Идемпотентно помечает сценарий как отправленный.

    Возвращает True, если запись создана (т.е. раньше не было), иначе False.
    """
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    ts = utc_now().replace(microsecond=0).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO funnel_events(user_id, scenario_key, sent_at_utc, meta) VALUES(?,?,?,?)",
            (int(user_id), str(scenario_key), ts, meta_json),
        )
        n = conn.execute("SELECT changes() AS n").fetchone()["n"]
        conn.commit()
    return int(n) == 1


def should_skip_sales(user_id: int) -> bool:
    return bool(is_sub_active(int(user_id)))


def eligible_demo_nopay_24h(user_id: int, now_utc: datetime | None = None) -> bool:
    """Условия сценария: было demo_ack, прошло >=24ч, не было оплат."""
    if should_skip_sales(user_id):
        return False

    now = (now_utc or utc_now()).replace(microsecond=0)
    with get_db() as conn:
        # есть ли ack
        row = conn.execute(
            "SELECT MAX(ack_at_utc) AS mx FROM demo_events WHERE user_id=? AND ack_at_utc IS NOT NULL",
            (int(user_id),),
        ).fetchone()
        if not row or not row["mx"]:
            return False
        try:
            ack = datetime.fromisoformat(str(row["mx"]))
            if ack.tzinfo is None:
                ack = ack.replace(tzinfo=timezone.utc)
        except ValueError:
            logging.getLogger(__name__).exception("Bad ISO timestamp in demo_events")
            return False

        if ack > (now - timedelta(hours=24)):
            return False

        paid = conn.execute(
            "SELECT 1 FROM payments WHERE user_id=? AND payload NOT LIKE 'gift:%' LIMIT 1",
            (int(user_id),),
        ).fetchone()
        if paid:
            return False

    return True


def eligible_expired_return_3d(user_id: int, now_utc: datetime | None = None) -> bool:
    """Условия сценария: подписка закончилась >=3 дня назад, сейчас не активна."""
    if should_skip_sales(user_id):
        return False

    now = (now_utc or utc_now()).replace(microsecond=0)
    with get_db() as conn:
        row = conn.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        if not row or not row["expires_at"]:
            return False
        try:
            exp = datetime.fromisoformat(str(row["expires_at"]))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except ValueError:
            logging.getLogger(__name__).exception("Bad ISO timestamp in subscriptions")
            return False

        if exp > now:
            return False
        if exp > (now - timedelta(days=3)):
            return False

    return True


def log_skip(user_id: int, scenario_key: str, reason: str):
    log_event(int(user_id), "funnel2_skipped", {"scenario": scenario_key, "reason": reason})
