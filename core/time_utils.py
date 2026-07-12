from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from config.settings import settings


def tzinfo(timezone_name: str | None = None) -> ZoneInfo:
    """Return an IANA timezone for the requested user/project context.

    Existing zero-argument callers keep project-timezone behaviour. User-journey
    code may pass a per-user timezone without reimplementing ``datetime.now`` and
    risking a second timezone contract.
    """

    name = str(timezone_name or settings.TIMEZONE or "UTC").strip() or "UTC"
    return ZoneInfo(name)


def now_tz(timezone_name: str | None = None) -> datetime:
    """Timezone-aware current datetime in the requested/project timezone."""

    return datetime.now(tzinfo(timezone_name))


def today_tz(timezone_name: str | None = None) -> date:
    """Current date in the requested/project timezone."""

    return now_tz(timezone_name).date()


def utc_now() -> datetime:
    """Timezone-aware UTC current datetime."""

    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    """UTC timestamp ISO-8601 without microseconds (tech marker)."""

    return utc_now().replace(microsecond=0).isoformat()


# Canonical helper name (v16.4+). Keep alias for backward compatibility.
def utc_now_iso() -> str:
    return utcnow_iso()


def normalize_utc_iso(value: str | datetime) -> str:
    """Normalize a datetime-like value to ISO-8601 UTC with tz offset."""

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
