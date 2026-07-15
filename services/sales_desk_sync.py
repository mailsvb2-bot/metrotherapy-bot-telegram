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

    joined_username = False
    joined_first_name = False
    if table_exists(conn, "users"):
        user_columns = table_columns(conn, "users")
        joined_username = "username" in user_columns
        joined_first_name = "first_name" in user_columns

    if joined_username and joined_first_name:
        select_sql = """
            SELECT e.*, u.username AS joined_username,
                   u.first_name AS joined_first_name
            FROM events e
            LEFT JOIN users u ON u.user_id=e.user_id
        """
    elif joined_username:
        select_sql = """
            SELECT e.*, u.username AS joined_username,
                   NULL AS joined_first_name
            FROM events e
            LEFT JOIN users u ON u.user_id=e.user_id
        """
    elif joined_first_name:
        select_sql = """
            SELECT e.*, NULL AS joined_username,
                   u.first_name AS joined_first_name
            FROM events e
            LEFT JOIN users u ON u.user_id=e.user_id
        """
    else:
        select_sql = """
            SELECT e.*, NULL AS joined_username,
                   NULL AS joined_first_name
            FROM events e
        """

    # The four SELECT prefixes above are fixed project SQL. Appending one of two
    # fixed order clauses avoids interpolating schema identifiers or user input.
    if "created_at" in event_columns:
        order_sql = " ORDER BY e.created_at DESC LIMIT ?"
    else:
        order_sql = " ORDER BY e.user_id DESC LIMIT ?"
    query = (
        select_sql
        + " WHERE e.user_id IS NOT NULL "
        + "AND e.name IN (?,?,?,?,?,?,?,?)"
        + order_sql
    )
    result = rows(
        conn.execute(
            query,
            (*_SALES_EVENT_NAMES, max(1, min(int(limit), 20000))),
        ).fetchall()
    )
    for item in result:
        item["username"] = item.pop("joined_username", None)
        item["first_name"] = item.pop("joined_first_name", None)
    return result


def _payment_rows(conn: Any) -> list[dict[str, Any]]:
    if not table_exists(conn, "payments"):
        return []
    columns = table_columns(conn, "payments")
    if "user_id" not in columns:
        return []

    canonical = {"provider_status", "amount", "currency", "created_at"}.issubset(columns)
    if canonical:
        return rows(
            conn.execute(
                """
                SELECT user_id,
                       SUM(COALESCE(amount, 0)) AS amount_units,
                       UPPER(COALESCE(currency, 'RUB')) AS currency,
                       MAX(created_at) AS paid_at
                FROM payments
                WHERE user_id IS NOT NULL
                  AND COALESCE(provider_status, 'succeeded')
                      IN ('succeeded','paid','success','captured')
                GROUP BY user_id, UPPER(COALESCE(currency, 'RUB'))
                """.strip()
            ).fetchall()
        )

    # Compatibility path for historical/minimal schemas. Aggregation happens in
    # Python so no column identifiers are interpolated into SQL.
    raw_rows = rows(
        conn.execute("SELECT * FROM payments WHERE user_id IS NOT NULL").fetchall()
    )
    status_column = "provider_status" if "provider_status" in columns else ("status" if "status" in columns else "")
    amount_column = "amount" if "amount" in columns else ("amount_minor" if "amount_minor" in columns else "")
    currency_column = "currency" if "currency" in columns else ""
    created_column = "created_at" if "created_at" in columns else ("paid_at" if "paid_at" in columns else "")
    successful = {"succeeded", "paid", "success", "captured"}
    aggregated: dict[tuple[int, str], dict[str, Any]] = {}
    for row in raw_rows:
        status = str(row.get(status_column) or "succeeded").strip().lower() if status_column else "succeeded"
        if status not in successful:
            continue
        user_id = int(row.get("user_id") or 0)
        currency = str(row.get(currency_column) or "RUB").strip().upper() if currency_column else "RUB"
        key = (user_id, currency)
        item = aggregated.setdefault(
            key,
            {"user_id": user_id, "amount_units": 0, "currency": currency, "paid_at": None},
        )
        item["amount_units"] += int(row.get(amount_column) or 0) if amount_column else 0
        paid_at = str(row.get(created_column) or "") if created_column else ""
        if paid_at:
            item["paid_at"] = max(str(item.get("paid_at") or ""), paid_at)
    return list(aggregated.values())


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
        "revenue_by_currency": {},
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
            user_id = int(row.get("user_id") or 0)
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
            user_id = int(payment.get("user_id") or 0)
        except (TypeError, ValueError):
            continue
        item = candidates.setdefault(
            user_id,
            _new_candidate(user_id, payment.get("paid_at"), now_iso=now_iso),
        )
        item["event_names"].append("payment_success")
        currency = str(clean_text(payment.get("currency"), limit=12, fallback="RUB") or "RUB").upper()
        item["revenue_by_currency"][currency] = max(0, int(payment.get("amount_units") or 0))
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

    revenue_by_currency = dict(candidate.get("revenue_by_currency") or {})
    # Keep the legacy columns as a RUB-only compatibility mirror.  All real
    # multi-currency reporting reads sales_lead_revenue.
    revenue_minor = max(0, int(revenue_by_currency.get("RUB") or 0))
    currency = "RUB"


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
            max(0, int((candidate.get("revenue_by_currency") or {}).get("RUB") or 0)),
            "RUB",
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


def _replace_revenue_rows(conn: Any, *, user_id: int, revenue_by_currency: dict[str, int], now_iso: str) -> None:
    normalized = {
        str(currency).upper(): max(0, int(amount or 0))
        for currency, amount in dict(revenue_by_currency or {}).items()
        if str(currency).strip() and int(amount or 0) > 0
    }
    conn.execute("DELETE FROM sales_lead_revenue WHERE user_id=?", (int(user_id),))
    for currency, amount in sorted(normalized.items()):
        conn.execute(
            "INSERT INTO sales_lead_revenue(user_id, currency, amount_units, updated_at) VALUES(?,?,?,?)",
            (int(user_id), currency[:12], amount, now_iso),
        )


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
        existing_user_ids = {
            int(row["user_id"] if hasattr(row, "keys") else row[0])
            for row in conn.execute(
                "SELECT user_id FROM sales_leads WHERE user_id IS NOT NULL"
            ).fetchall()
        }
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
                    _replace_revenue_rows(
                        conn, user_id=user_id,
                        revenue_by_currency=dict(candidate.get("revenue_by_currency") or {}),
                        now_iso=now_iso,
                    )
                    inserted += 1
                    continue

                _replace_revenue_rows(
                    conn, user_id=user_id,
                    revenue_by_currency=dict(candidate.get("revenue_by_currency") or {}),
                    now_iso=now_iso,
                )
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

            # Leads with no current successful payment still need their revenue
            # projection cleared after a refund/cancellation. Do not synthesize a
            # candidate for them: that would overwrite attribution or manual stage
            # fields with default values.
            for user_id in sorted(existing_user_ids - set(candidates)):
                _replace_revenue_rows(
                    conn,
                    user_id=user_id,
                    revenue_by_currency={},
                    now_iso=now_iso,
                )
                conn.execute(
                    """
                    UPDATE sales_leads
                    SET revenue_minor=0, currency='RUB', updated_at=?
                    WHERE user_id=? AND revenue_minor<>0
                    """.strip(),
                    (now_iso, user_id),
                )
    return {"inserted": inserted, "updated": updated, "promoted": promoted}
