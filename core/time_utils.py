from __future__ import annotations

from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

from config.settings import settings


def tzinfo() -> ZoneInfo:
    """Project timezone (IANA) configured via settings.TIMEZONE."""
    return ZoneInfo(settings.TIMEZONE)


def now_tz() -> datetime:
    """Timezone-aware current datetime in project timezone."""
    return datetime.now(tzinfo())


def today_tz() -> date:
    """Current date in project timezone."""
    return now_tz().date()


def utc_now() -> datetime:
    """Timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)



def utcnow_iso() -> str:
    """UTC timestamp ISO-8601 without microseconds (tech marker)."""
    return utc_now().replace(microsecond=0).isoformat()


# Canonical helper name (v16.4+). Keep alias for backward compatibility.
def utc_now_iso() -> str:
    return utcnow_iso()


def normalize_utc_iso(value: str | datetime) -> str:
    """Normalize a datetime-like value to ISO-8601 UTC with tz offset.

    We store UTC timestamps as ISO strings to keep DB schema simple.
    This helper prevents subtle bugs where someone accidentally stores:
    - naive ISO strings without timezone
    - strings with microseconds
    """
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

def to_epoch_seconds(dt: datetime) -> int:
    """Convert aware/naive datetime to epoch seconds (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())
