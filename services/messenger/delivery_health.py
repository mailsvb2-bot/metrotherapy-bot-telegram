from __future__ import annotations

from datetime import datetime
from typing import Any

from core.time_utils import utc_now
from services.db import db
from services.messenger import delivery_outbox


def _row_value(row: Any, key: str, index: int) -> Any:
    return row[key] if hasattr(row, "keys") else row[index]


def _age_sec(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return 0
    now = utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return max(0, int((now - parsed).total_seconds()))


def _queue_age_snapshot() -> dict[str, int]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT
                MIN(CASE WHEN status='pending' THEN created_at END) AS oldest_pending,
                MIN(CASE WHEN status='retry' THEN created_at END) AS oldest_retry,
                MIN(CASE WHEN status='sending' THEN locked_at END) AS oldest_sending
            FROM messenger_delivery_outbox
            """.strip()
        ).fetchone()
    if row is None:
        return {
            "oldest_pending_age_sec": 0,
            "oldest_retry_age_sec": 0,
            "oldest_sending_age_sec": 0,
        }
    return {
        "oldest_pending_age_sec": _age_sec(_row_value(row, "oldest_pending", 0)),
        "oldest_retry_age_sec": _age_sec(_row_value(row, "oldest_retry", 1)),
        "oldest_sending_age_sec": _age_sec(_row_value(row, "oldest_sending", 2)),
    }


def delivery_health_snapshot() -> dict[str, Any]:
    counts = delivery_outbox.outbox_snapshot()
    worker = getattr(delivery_outbox, "_worker_task", None)
    stop_event = getattr(delivery_outbox, "_worker_stop", None)
    expected = stop_event is not None
    active = bool(worker is not None and not worker.done())
    # Before the HTTP runtime starts, this function is also used as a pure config
    # preflight. Once start_delivery_worker() declares the worker expected, a
    # stopped/crashed task becomes a real readiness failure.
    healthy = active if expected else True
    return {
        "worker_expected": expected,
        "worker_active": active,
        "worker_running": healthy,
        "pending": int(counts.get("pending", 0)),
        "retry": int(counts.get("retry", 0)),
        "sending": int(counts.get("sending", 0)),
        "sent": int(counts.get("sent", 0)),
        "dead": int(counts.get("dead", 0)),
        **_queue_age_snapshot(),
    }
