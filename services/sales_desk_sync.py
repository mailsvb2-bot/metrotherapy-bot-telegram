from __future__ import annotations

import logging
from typing import Any

from services.db import db, tx
from services.migrations._helpers import table_exists
from services.sales_desk_core import (
    compact_display_name,
    extract_attribution,
    lead_key,
    normalize_stage,
    should_auto_advance,
    stage_from_event_names,
)
from services.sales_desk_db import (
    audit,
    changed_count,
    clean_text,
    ensure_schema,
    fetch_lead,
    lead_snapshot,
    rowdict,
    rows,
    table_columns,
)

log = logging.getLogger(__name__)

_SALES_EVENT_NAMES = (
    "funnel_start_command",
    "demo_sent",
    "demo_ack",
    "sub_menu_open",
    "funnel_tariffs_command",
    "payment_started",
    "payment_success",
    "gift_paid",
)


def _event_rows(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not table_exists(conn, "events"):
        return []
    event_columns = table_columns(conn, "events")
    if not {"user_id", "name"}.issubset(event_columns):
        return []

    select = ["e.user_id", "e.name"]
    for column in ("created_at", "meta", "payload"):
        selected = f"e.{column}" if column in event_columns else f"NULL AS {column}"
        select.append(selected)

    join = ""
    if table_exists(conn, "users"):
        user_columns = table_columns(conn, "users")
        join = " LEFT JOIN users u ON u.user_id=e.user_id"
        select.append(
            "u.username" if "username" in user_columns else "NULL AS username"
        )
        select.append(
            "u.first_name" if "first_name" in user_columns else "NULL AS first_name"
        )
    else:
        select.extend(["NULL AS username", "NULL AS first_name"])

    placeholders = ",".join("?" for _ in _SALES_EVENT_NAMES)
    order_column = "e.created_at" if "created_at" in event_columns else "e.user_id"
    return rows(
        conn.execute(
            f"""
            SELECT {', '.join(select)}
            FROM events e{join}
            WHERE e.user_id IS NOT NULL
              AND e.name IN ({placeholders})
            ORDER BY {order_column} DESC
            LIMIT ?
            """.strip(),
            (*_SALES_EVENT_NAMES, max(1, min(int(limit), 20000))),
        ).fetchall()
    )


def _payment_rows(conn: Any) -> list[dict[str, Any]]:
    if not table_exists(conn, "payments"):
        return []
    columns = table_columns(conn, "payments")
    if "user_id" not in columns:
        return []

    status_column = (
        "provider_status"
        if "provider_status" in columns
        else ("status" if "status" in columns else "")
    )
    amount_column = (
        "amount"
        if "amount" in columns
        else ("amount_minor" if "amount_minor" in columns else "")
    )
    currency_column = "currency" if "currency" in columns else ""
    created_column = (
        "created_at"
        if "created_at" in columns
        else ("paid_at" if "paid_at" in columns else "")
    )

    conditions = ["user_id IS NOT NULL"]
    if status_column:
        conditions.append(
            f"COALESCE({status_column}, 'succeeded') "
            "IN ('succeeded','paid','success','captured')"
        )

    amount_expression = f"COALESCE({amount_column}, 0)" if amount_column else "0"
    currency_expression = (
        f"COALESCE(MAX({currency_column}), 'RUB')"
        if currency_column
        else "'RUB'"
    )
    paid_expression = f"MAX({created_column})" if created_column else "NULL"
    return rows(
        conn.execute(
            f"""
            SELECT user_id,
                   SUM({amount_expression}) AS revenue_minor,
                   {currency_expression} AS currency,
                   {paid_expression} AS paid_at
            FROM payments
            WHERE {' AND '.join(conditions)}
            GROUP BY user_id
            """.strip()
        ).fetchall()
    )


def _new_candidate(user_id: int, activity_at: Any, *, now_iso: str) -> dict[str, Any]:
    timestamp = str(activity_at or now_iso)
    return {
        "user_id": int(user_id),
        "event_names": [],
        "first_name": None,
        "username": None,
        "source": "organic",
        "campaign": "",
        "creative": "",
        "first_activity_at": timestamp,
        "last_activity_at": timestamp,
        "revenue_minor": 0,
        "currency": "RUB",
    }


def _candidate_map(
    conn: Any,
    *,
    limit: int,
    now_iso: str,
) -> dict[int, dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    for row in _event_rows(conn, limit=limit):
        try:
            user_id = int(row.get("user_id"))
        except (TypeError, ValueError):
            continue

        item = candidates.setdefault(
            user_id,
            _new_candidate(user_id, row.get("created_at"), now_iso=now_iso),
        )
        item["event_names"].append(str(row.get("name") or ""))
        if row.get("first_name") and not item.get("first_name"):
            item["first_name"] = row.get("first_name")
        if row.get("username") and not item.get("username"):
            item["username"] = row.get("username")

        created_at = str(row.get("created_at") or "")
        if created_at:
            item["first_activity_at"] = min(
                str(item.get("first_activity_at") or created_at),
                created_at,
            )
            item["last_activity_at"] = max(
                str(item.get("last_activity_at") or created_at),
                created_at,
            )

        attribution = extract_attribution(row.get("meta"), row.get("payload"))
        if item.get("source") == "organic" and attribution["source"] != "organic":
            item.update(attribution)

    for payment in _payment_rows(conn):
        try:
            user_id = int(payment.get("user_id"))
        except (TypeError, ValueError):
            continue
        item = candidates.setdefault(
            user_id,
            _new_candidate(user_id, payment.get("paid_at"), now_iso=now_iso),
        )
        item["event_names"].append("payment_success")
        item["revenue_minor"] = max(
            0,
            int(payment.get("revenue_minor") or 0),
        )
        item["currency"] = clean_text(
            payment.get("currency"),
            limit=12,
            fallback="RUB",
        )
        if payment.get("paid_at"):
            item["last_activity_at"] = max(
                str(item.get("last_activity_at") or ""),
                str(payment.get("paid_at") or ""),
            )
    return candidates


def _candidate_projection(
    existing: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    incoming_stage = stage_from_event_names(candidate["event_names"])
    current_stage = normalize_stage(str(existing.get("stage") or "new"))
    auto_promoted = should_auto_advance(
        current_stage,
        incoming_stage,
        stage_source=str(existing.get("stage_source") or "auto"),
    )
    target_stage = incoming_stage if auto_promoted else current_stage
    target_stage_source = (
        "auto" if auto_promoted else str(existing.get("stage_source") or "auto")
    )

    incoming_revenue = max(0, int(candidate.get("revenue_minor") or 0))
    existing_revenue = max(0, int(existing.get("revenue_minor") or 0))
    revenue_minor = max(existing_revenue, incoming_revenue)
    currency = (
        clean_text(candidate.get("currency"), limit=12, fallback="RUB")
        if incoming_revenue >= existing_revenue
        else clean_text(existing.get("currency"), limit=12, fallback="RUB")
    )

    projection = {
        "display_name": compact_display_name(
            first_name=candidate.get("first_name"),
            username=candidate.get("username"),
            user_id=candidate.get("user_id"),
        ),
        "username": clean_text(
            candidate.get("username"),
            limit=100,
            fallback=clean_text(existing.get("username"), limit=100),
        ),
        "source": clean_text(
            candidate.get("source"),
            limit=100,
            fallback=clean_text(
                existing.get("source"),
                limit=100,
                fallback="organic",
            ),
        ),
        "campaign": clean_text(
            candidate.get("campaign"),
            limit=160,
            fallback=clean_text(existing.get("campaign"), limit=160),
        ),
        "creative": clean_text(
            candidate.get("creative"),
            limit=160,
            fallback=clean_text(existing.get("creative"), limit=160),
        ),
        "stage": target_stage,
        "stage_source": target_stage_source,
        "last_activity_at": max(
            str(existing.get("last_activity_at") or ""),
            str(candidate.get("last_activity_at") or ""),
        )
        or None,
        "revenue_minor": revenue_minor,
        "currency": currency or "RUB",
    }
    return projection, auto_promoted


def _projection_changed(
    existing: dict[str, Any],
    projection: dict[str, Any],
) -> bool:
    for key, value in projection.items():
        current = existing.get(key)
        if key == "revenue_minor":
            if int(current or 0) != int(value or 0):
                return True
            continue
        if (current or None) != (value or None):
            return True
    return False


def _insert_candidate(
    conn: Any,
    candidate: dict[str, Any],
    *,
    now_iso: str,
) -> dict[str, Any]:
    user_id = int(candidate["user_id"])
    created_at = str(candidate.get("first_activity_at") or now_iso)
    stage = stage_from_event_names(candidate["event_names"])
    conn.execute(
        """
        INSERT INTO sales_leads(
            lead_key, user_id, display_name, username, source, campaign,
            creative, stage, stage_source, last_activity_at,
            revenue_minor, currency, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """.strip(),
        (
            lead_key(user_id),
            user_id,
            compact_display_name(
                first_name=candidate.get("first_name"),
                username=candidate.get("username"),
                user_id=user_id,
            ),
            clean_text(candidate.get("username"), limit=100),
            clean_text(
                candidate.get("source"),
                limit=100,
                fallback="organic",
            ),
            clean_text(candidate.get("campaign"), limit=160),
            clean_text(candidate.get("creative"), limit=160),
            stage,
            "auto",
            str(candidate.get("last_activity_at") or created_at),
            max(0, int(candidate.get("revenue_minor") or 0)),
            clean_text(candidate.get("currency"), limit=12, fallback="RUB"),
            created_at,
            now_iso,
        ),
    )
    created = rowdict(
        conn.execute(
            "SELECT * FROM sales_leads WHERE lead_key=? LIMIT 1",
            (lead_key(user_id),),
        ).fetchone()
    )
    if not created:
        raise RuntimeError("sales_lead_insert_failed")
    audit(
        conn,
        lead_id=int(created["id"]),
        event_type="lead_discovered",
        actor_id=0,
        before=None,
        after=lead_snapshot(created),
        details={"events": sorted(set(candidate["event_names"]))},
        created_at=now_iso,
    )
    return created


def sync_sales_leads(
    *,
    limit: int = 5000,
    now_iso: str,
) -> dict[str, int]:
    inserted = 0
    updated = 0
    promoted = 0
    with db() as conn:
        ensure_schema(conn)
        candidates = _candidate_map(conn, limit=limit, now_iso=now_iso)
        with tx(conn):
            for user_id, candidate in candidates.items():
                key = lead_key(user_id)
                existing = rowdict(
                    conn.execute(
                        "SELECT * FROM sales_leads WHERE lead_key=? LIMIT 1",
                        (key,),
                    ).fetchone()
                )
                if not existing:
                    _insert_candidate(conn, candidate, now_iso=now_iso)
                    inserted += 1
                    continue

                projection, auto_promoted = _candidate_projection(existing, candidate)
                if not _projection_changed(existing, projection):
                    continue

                before = lead_snapshot(existing)
                conn.execute(
                    """
                    UPDATE sales_leads
                    SET display_name=?, username=?, source=?, campaign=?,
                        creative=?, stage=?, stage_source=?, last_activity_at=?,
                        revenue_minor=?, currency=?, updated_at=?, version=version+1
                    WHERE id=? AND version=?
                    """.strip(),
                    (
                        projection["display_name"],
                        projection["username"],
                        projection["source"],
                        projection["campaign"],
                        projection["creative"],
                        projection["stage"],
                        projection["stage_source"],
                        projection["last_activity_at"],
                        projection["revenue_minor"],
                        projection["currency"],
                        now_iso,
                        int(existing["id"]),
                        int(existing.get("version") or 1),
                    ),
                )
                if changed_count(conn) != 1:
                    log.info(
                        "Sales lead sync skipped concurrent update: lead_id=%s",
                        existing.get("id"),
                    )
                    continue

                after = fetch_lead(conn, int(existing["id"]))
                if auto_promoted:
                    audit(
                        conn,
                        lead_id=int(existing["id"]),
                        event_type="stage_auto_advanced",
                        actor_id=0,
                        before=before,
                        after=lead_snapshot(after),
                        details={"incoming_stage": projection["stage"]},
                        created_at=now_iso,
                    )
                    promoted += 1
                updated += 1
    return {"inserted": inserted, "updated": updated, "promoted": promoted}
