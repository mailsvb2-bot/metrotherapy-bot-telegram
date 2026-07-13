from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from config.settings import settings
from services.jobs import add_job as enqueue_job
from services.roles import ROLE_ADMIN, ROLE_MARKETING, user_roles


def tzinfo() -> ZoneInfo:
    return ZoneInfo(getattr(settings, "TIMEZONE", "UTC") or "UTC")


def parse_hhmm(s: str) -> tuple[int, int] | None:
    raw = (s or "").strip()
    if ":" not in raw:
        return None
    hh, mm = raw.split(":", 1)
    if not (hh.isdigit() and mm.isdigit()):
        return None
    hour, minute = int(hh), int(mm)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def add_job(user_id: int, job_type: str, run_at_utc_iso: str, payload: dict) -> bool:
    """Compatibility adapter to the canonical idempotent job queue."""

    return enqueue_job(
        int(user_id),
        str(job_type),
        str(run_at_utc_iso),
        dict(payload or {}),
    )


def is_superadmin(uid: int) -> bool:
    try:
        return int(uid) in set(settings.admin_id_list)
    except TypeError:
        logging.getLogger(__name__).exception("Superadmin check failed")
        return False
    except ValueError:
        logging.getLogger(__name__).exception("Superadmin check failed")
        return False
    except AttributeError:
        logging.getLogger(__name__).exception("Superadmin check failed")
        return False


def is_marketing(uid: int) -> bool:
    if is_superadmin(uid):
        return True
    roles = user_roles(uid)
    return ROLE_MARKETING in roles or ROLE_ADMIN in roles
