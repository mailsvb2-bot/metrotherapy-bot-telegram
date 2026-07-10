from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from services.db import db
from services.growth_conversion_hub_core import (
    build_dry_run_conversion,
    format_conversion_hub_report,
    payment_conversion_type,
    stable_json,
)
from services.migrations._helpers import table_exists

log = logging.getLogger(__name__)

_PERIOD_DAYS: dict[str, int | None] = {
    "today": 0,
    "week": 7,
    "month": 30,
    "all": None,
}


@dataclass(frozen=True)
class ConversionEnqueueResult:
    inserted: bool
    idempotency_key: str
    row_id: int = 0
    error: str = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def normalize_period(period: str | None) -> str:
    value = str(period or "today").strip().lower()
    return value if value in _PERIOD_DAYS else "today"


def _period_start(period: str) -> str | None:
    normalized = normalize_period(period)
    if normalized == "today":
        now = _utc_now()
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    days = _PERIOD_DAYS.get(normalized)
    if days is None:
        return None
    return (_utc_now() - timedelta(days=int(days))).isoformat()


def ensure_schema(conn: Any) -> None:
    if not table_exists(conn, "growth_conversion_outbox"):
        raise RuntimeError("growth_conversion_outbox_schema_not_migrated")


def _insert_dry_run_item(conn: Any, item: dict[str, Any]) -> ConversionEnqueueResult:
    ensure_schema(conn)
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO growth_conversion_outbox(
            conversion_type, source_platform, source_event, external_event_id,
            user_id, amount_minor, currency, attribution_json, payload_json,
            target_provider, mode, status, dispatch_allowed, idempotency_key
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """.strip(),
        (
            item["conversion_type"],
            item["source_platform"],
            item["source_event"],
            item["external_event_id"],
            item["user_id"],
            item["amount_minor"],
            item["currency"],
            stable_json(dict(item["attribution"])),
            stable_json(dict(item["payload"])),
            item["target_provider"],
            item["mode"],
            item["status"],
            1 if item["dispatch_allowed"] else 0,
            item["idempotency_key"],
        ),
    )
    inserted = int(getattr(cursor, "rowcount", 0) or 0) > 0
    row = conn.execute(
        "SELECT id FROM growth_conversion_outbox WHERE idempotency_key=? LIMIT 1",
        (item["idempotency_key"],),
    ).fetchone()
    row_id = int(row["id"] if hasattr(row, "keys") else row[0]) if row is not None else 0
    return ConversionEnqueueResult(
        inserted=inserted,
        idempotency_key=str(item["idempotency_key"]),
        row_id=row_id,
    )


def enqueue_conversion_dry_run_tx(
    conn: Any,
    *,
    conversion_type: str,
    source_platform: Any,
    source_event: Any,
    external_event_id: Any,
    user_id: Any = 0,
    amount_minor: Any = 0,
    currency: Any = "RUB",
    attribution: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    target_provider: Any = "none",
) -> ConversionEnqueueResult:
    item = build_dry_run_conversion(
        conversion_type=conversion_type,
        source_platform=source_platform,
        source_event=source_event,
        external_event_id=external_event_id,
        user_id=user_id,
        amount_minor=amount_minor,
        currency=currency,
        attribution=attribution,
        payload=payload,
        target_provider=target_provider,
    )
    return _insert_dry_run_item(conn, item)


def enqueue_conversion_dry_run(
    *,
    conversion_type: str,
    source_platform: Any,
    source_event: Any,
    external_event_id: Any,
    user_id: Any = 0,
    amount_minor: Any = 0,
    currency: Any = "RUB",
    attribution: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    target_provider: Any = "none",
) -> ConversionEnqueueResult:
    with db() as conn:
        return enqueue_conversion_dry_run_tx(
            conn,
            conversion_type=conversion_type,
            source_platform=source_platform,
            source_event=source_event,
            external_event_id=external_event_id,
            user_id=user_id,
            amount_minor=amount_minor,
            currency=currency,
            attribution=attribution,
            payload=payload,
            target_provider=target_provider,
        )


def _failed_enqueue_result(
    exc: BaseException,
    *,
    source_platform: str,
    source_event: str,
    external_event_id: str,
) -> ConversionEnqueueResult:
    log.warning(
        "Growth conversion dry-run enqueue skipped: source=%s event=%s external_event_id=%s error=%s",
        source_platform,
        source_event,
        external_event_id,
        type(exc).__name__,
    )
    return ConversionEnqueueResult(
        inserted=False,
        idempotency_key="",
        error=f"{type(exc).__name__}:{exc}",
    )


def record_payment_conversion_dry_run_safe(
    *,
    source_platform: str,
    source_event: str,
    external_event_id: str,
    user_id: int,
    amount_minor: int,
    currency: str,
    gift: bool = False,
    attribution: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> ConversionEnqueueResult:
    """Best-effort payment ingestion that can never fail the payment path."""

    try:
        return enqueue_conversion_dry_run(
            conversion_type=payment_conversion_type(gift=gift),
            source_platform=source_platform,
            source_event=source_event,
            external_event_id=external_event_id,
            user_id=user_id,
            amount_minor=amount_minor,
            currency=currency,
            attribution=attribution,
            payload=payload,
            target_provider="none",
        )
    except sqlite3.Error as exc:
        return _failed_enqueue_result(
            exc,
            source_platform=source_platform,
            source_event=source_event,
            external_event_id=external_event_id,
        )
    except OSError as exc:
        return _failed_enqueue_result(
            exc,
            source_platform=source_platform,
            source_event=source_event,
            external_event_id=external_event_id,
        )
    except RuntimeError as exc:
        return _failed_enqueue_result(
            exc,
            source_platform=source_platform,
            source_event=source_event,
            external_event_id=external_event_id,
        )
    except (TypeError, ValueError) as exc:
        return _failed_enqueue_result(
            exc,
            source_platform=source_platform,
            source_event=source_event,
            external_event_id=external_event_id,
        )


def _rowdict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {}


def conversion_hub_snapshot(period: str = "today", *, limit: int = 20) -> dict[str, Any]:
    normalized_period = normalize_period(period)
    start = _period_start(normalized_period)
    with db() as conn:
        ensure_schema(conn)
        where = "WHERE mode='dry_run'"
        params: list[Any] = []
        if start:
            where += " AND COALESCE(created_at, '') >= ?"
            params.append(start)
        rows = conn.execute(
            f"""
            SELECT id, conversion_type, source_platform, source_event, external_event_id,
                   user_id, amount_minor, currency, target_provider, mode, status,
                   dispatch_allowed, idempotency_key, created_at
            FROM growth_conversion_outbox
            {where}
            ORDER BY id DESC
            LIMIT ?
            """.strip(),
            tuple(params + [int(limit)]),
        ).fetchall()
        count_rows = conn.execute(
            f"""
            SELECT conversion_type, COUNT(*) AS n
            FROM growth_conversion_outbox
            {where}
            GROUP BY conversion_type
            """.strip(),
            tuple(params),
        ).fetchall()

    latest = [_rowdict(row) for row in rows]
    counts = {
        str(row["conversion_type"] if hasattr(row, "keys") else row[0]):
        int((row["n"] if hasattr(row, "keys") else row[1]) or 0)
        for row in count_rows
    }
    return {
        "ok": True,
        "period": normalized_period,
        "mode": "dry_run",
        "dispatch_allowed": False,
        "total": sum(counts.values()),
        "counts": counts,
        "latest": latest,
    }


def _degraded_report(period: str, exc: BaseException) -> str:
    log.warning("Conversion Hub report degraded: %s", type(exc).__name__)
    return "\n".join([
        "🧪 Conversion Hub",
        "",
        f"Период: {period}",
        "Статус: DEGRADED",
        f"Причина: {type(exc).__name__}:{exc}",
        "",
        "Safety lock:",
        "— основной платёжный и пользовательский контур продолжает работу;",
        "— postback sender отсутствует;",
        "— dispatch_allowed=False.",
    ])


def build_conversion_hub_report(period: str = "today") -> str:
    normalized_period = normalize_period(period)
    try:
        return format_conversion_hub_report(conversion_hub_snapshot(normalized_period))
    except sqlite3.Error as exc:
        return _degraded_report(normalized_period, exc)
    except OSError as exc:
        return _degraded_report(normalized_period, exc)
    except RuntimeError as exc:
        return _degraded_report(normalized_period, exc)
    except (TypeError, ValueError) as exc:
        return _degraded_report(normalized_period, exc)
