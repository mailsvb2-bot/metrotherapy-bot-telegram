from __future__ import annotations

import json
from typing import Any

from services.migrations._helpers import table_exists


class SalesDeskUnavailable(RuntimeError):
    pass


def rowdict(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {}


def rows(items: Any) -> list[dict[str, Any]]:
    return [rowdict(item) for item in list(items or [])]


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def clean_text(
    value: Any,
    *,
    limit: int,
    fallback: str | None = None,
) -> str | None:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[: max(1, int(limit))]


def table_columns(conn: Any, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def ensure_schema(conn: Any) -> None:
    required = (
        "sales_leads",
        "sales_lead_notes",
        "sales_lead_audit",
        "sales_outbound_messages",
        "sales_lead_revenue",
    )
    for table in required:
        if not table_exists(conn, table):
            raise SalesDeskUnavailable(f"{table}_schema_not_migrated")


def lead_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "stage": str(row.get("stage") or "new"),
        "stage_source": str(row.get("stage_source") or "auto"),
        "assigned_to": row.get("assigned_to"),
        "next_contact_at": row.get("next_contact_at"),
        "last_contact_at": row.get("last_contact_at"),
        "revenue_minor": int(row.get("revenue_minor") or 0),
        "version": int(row.get("version") or 1),
    }


def audit(
    conn: Any,
    *,
    lead_id: int,
    event_type: str,
    actor_id: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    details: dict[str, Any] | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO sales_lead_audit(
            lead_id, event_type, actor_id, before_json, after_json,
            details_json, created_at
        ) VALUES(?,?,?,?,?,?,?)
        """.strip(),
        (
            int(lead_id),
            str(event_type),
            int(actor_id),
            stable_json(before or {}),
            stable_json(after or {}),
            stable_json(details or {}),
            str(created_at),
        ),
    )


def changed_count(conn: Any) -> int:
    row = rowdict(conn.execute("SELECT changes() AS n").fetchone())
    return int(row.get("n") or row.get("c") or 0)


def fetch_lead(conn: Any, lead_id: int) -> dict[str, Any]:
    return rowdict(
        conn.execute(
            "SELECT * FROM sales_leads WHERE id=? LIMIT 1",
            (int(lead_id),),
        ).fetchone()
    )
