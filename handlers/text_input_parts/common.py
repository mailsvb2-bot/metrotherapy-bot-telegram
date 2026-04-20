from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config.settings import settings
from services.db import get_db
from services.roles import user_roles, ROLE_ADMIN, ROLE_MARKETING


def tzinfo() -> ZoneInfo:
    # В .env должно быть Europe/Moscow
    return ZoneInfo(getattr(settings, "TIMEZONE", "Europe/Moscow"))


def parse_hhmm(s: str) -> tuple[int, int] | None:
    s = (s or "").strip()
    if ":" not in s:
        return None
    hh, mm = s.split(":", 1)
    if not (hh.isdigit() and mm.isdigit()):
        return None
    h, m = int(hh), int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        return None
    return h, m


def add_job(user_id: int, job_type: str, run_at_utc_iso: str, payload: dict):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO jobs(user_id, job_type, run_at_utc, payload) VALUES(?,?,?,?)",
            (int(user_id), str(job_type), str(run_at_utc_iso), json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()


def is_superadmin(uid: int) -> bool:
    try:
        return int(uid) in set(settings.admin_id_list)
    except (TypeError, ValueError, AttributeError):
        logging.getLogger(__name__).exception("Superadmin check failed")
        return False


def is_marketing(uid: int) -> bool:
    if is_superadmin(uid):
        return True
    rs = user_roles(uid)
    return (ROLE_MARKETING in rs) or (ROLE_ADMIN in rs)
