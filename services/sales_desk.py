from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from services.db import db, tx
from services.migrations._helpers import table_exists
from services.sales_desk_core import (
    SALES_STAGES,
    assert_transition,
    compact_display_name,
    extract_attribution,
    lead_key,
    normalize_filter,
    normalize_stage,
    sanitize_note,
    should_auto_advance,
    stage_from_event_names,
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

_OPEN_STAGE_SQL = "'new','contacted','qualified','checkout'"
_SYNC_INTERVAL_SECONDS = 30.0
_SYNC_LOCK = threading.Lock()
_LAST_SYNC_MONOTONIC = 0.0

_STAGE_TITLES = {
    "new": "Новый",
    "contacted": "Связались",
    "qualified": "Заинтересован",
    "checkout": "Оплата начата",
    "won": "Оплатил",
    "lost": "Отказ",
}


class SalesDeskUnavailable(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def _rowdict(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {}


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [_rowdict(row) for row in list(rows or [])]


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _clean_text(value: Any, *, limit: int, fallback: str | None = None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[: max(1, int(limit))]


def _table_columns(conn: Any, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def ensure_schema(conn: Any) -> None:
    for table in ("sales_leads", "sales_lead_notes", "sales_lead_audit"):
        if not table_exists(conn, table):
            raise SalesDeskUnavailable(f"{table}_schema_not_migrated")


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
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


def _audit(
    conn: Any,
    *,
    lead_id: int,
    event_type: str,
    actor_id: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    details: dict[str, Any] | None = None,
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
            _stable_json(before or {}),
            _stable_json(after or {}),
            _stable_json(details or {}),
            _iso(),
        ),
    )


def _changed_count(conn: Any) -> int:
    row = _rowdict(conn.execute("SELECT changes() AS n").fetchone())
    return int(row.get("n") or row.get("c") or 0)


def _fetch_lead(conn: Any, lead_id: int) -> dict[str, Any]:
    return _rowdict(
        conn.execute(
            "SELECT * FROM sales_leads WHERE id=? LIMIT 1",
            (int(lead_id),),
        ).fetchone()
    )


def _event_rows(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not table_exists(conn, "events"):
        return []
    event_columns = _table_columns(conn, "events")
    if not {"user_id", "name"}.issubset(event_columns):
        return []

    select = ["e.user_id", "e.name"]
    for column in ("created_at", "meta", "payload"):
        selected = f"e.{column}" if column in event_columns else f"NULL AS {column}"
        select.append(selected)

    join = ""
    if table_exists(conn, "users"):
        user_columns = _table_columns(conn, "users")
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
    return _rows(
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
    columns = _table_columns(conn, "payments")
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
    return _rows(
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


def _new_candidate(user_id: int, activity_at: Any) -> dict[str, Any]:
    timestamp = str(activity_at or _iso())
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


def _candidate_map(conn: Any, *, limit: int) -> dict[int, dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    for row in _event_rows(conn, limit=limit):
        try:
            user_id = int(row.get("user_id"))
        except (TypeError, ValueError):
            continue

        item = candidates.setdefault(
            user_id,
            _new_candidate(user_id, row.get("created_at")),
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
            _new_candidate(user_id, payment.get("paid_at")),
        )
        item["event_names"].append("payment_success")
        item["revenue_minor"] = max(
            0,
            int(payment.get("revenue_minor") or 0),
        )
        item["currency"] = _clean_text(
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
        _clean_text(candidate.get("currency"), limit=12, fallback="RUB")
        if incoming_revenue >= existing_revenue
        else _clean_text(existing.get("currency"), limit=12, fallback="RUB")
    )

    projection = {
        "display_name": compact_display_name(
            first_name=candidate.get("first_name"),
            username=candidate.get("username"),
            user_id=candidate.get("user_id"),
        ),
        "username": _clean_text(
            candidate.get("username"),
            limit=100,
            fallback=_clean_text(existing.get("username"), limit=100),
        ),
        "source": _clean_text(
            candidate.get("source"),
            limit=100,
            fallback=_clean_text(existing.get("source"), limit=100, fallback="organic"),
        ),
        "campaign": _clean_text(
            candidate.get("campaign"),
            limit=160,
            fallback=_clean_text(existing.get("campaign"), limit=160),
        ),
        "creative": _clean_text(
            candidate.get("creative"),
            limit=160,
            fallback=_clean_text(existing.get("creative"), limit=160),
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


def _insert_candidate(conn: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    user_id = int(candidate["user_id"])
    created_at = str(candidate.get("first_activity_at") or _iso())
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
            _clean_text(candidate.get("username"), limit=100),
            _clean_text(
                candidate.get("source"),
                limit=100,
                fallback="organic",
            ),
            _clean_text(candidate.get("campaign"), limit=160),
            _clean_text(candidate.get("creative"), limit=160),
            stage,
            "auto",
            str(candidate.get("last_activity_at") or created_at),
            max(0, int(candidate.get("revenue_minor") or 0)),
            _clean_text(candidate.get("currency"), limit=12, fallback="RUB"),
            created_at,
            _iso(),
        ),
    )
    created = _rowdict(
        conn.execute(
            "SELECT * FROM sales_leads WHERE lead_key=? LIMIT 1",
            (lead_key(user_id),),
        ).fetchone()
    )
    if not created:
        raise RuntimeError("sales_lead_insert_failed")
    _audit(
        conn,
        lead_id=int(created["id"]),
        event_type="lead_discovered",
        actor_id=0,
        before=None,
        after=_snapshot(created),
        details={"events": sorted(set(candidate["event_names"]))},
    )
    return created


def sync_sales_leads(*, limit: int = 5000) -> dict[str, int]:
    inserted = 0
    updated = 0
    promoted = 0
    with db() as conn:
        ensure_schema(conn)
        candidates = _candidate_map(conn, limit=limit)
        with tx(conn):
            for user_id, candidate in candidates.items():
                key = lead_key(user_id)
                existing = _rowdict(
                    conn.execute(
                        "SELECT * FROM sales_leads WHERE lead_key=? LIMIT 1",
                        (key,),
                    ).fetchone()
                )
                if not existing:
                    _insert_candidate(conn, candidate)
                    inserted += 1
                    continue

                projection, auto_promoted = _candidate_projection(existing, candidate)
                if not _projection_changed(existing, projection):
                    continue

                before = _snapshot(existing)
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
                        _iso(),
                        int(existing["id"]),
                        int(existing.get("version") or 1),
                    ),
                )
                if _changed_count(conn) != 1:
                    log.info(
                        "Sales lead sync skipped concurrent update: lead_id=%s",
                        existing.get("id"),
                    )
                    continue

                after = _fetch_lead(conn, int(existing["id"]))
                if auto_promoted:
                    _audit(
                        conn,
                        lead_id=int(existing["id"]),
                        event_type="stage_auto_advanced",
                        actor_id=0,
                        before=before,
                        after=_snapshot(after),
                        details={"incoming_stage": projection["stage"]},
                    )
                    promoted += 1
                updated += 1
    return {"inserted": inserted, "updated": updated, "promoted": promoted}


def _sync_if_due() -> None:
    global _LAST_SYNC_MONOTONIC

    now = time.monotonic()
    if now - _LAST_SYNC_MONOTONIC < _SYNC_INTERVAL_SECONDS:
        return
    if not _SYNC_LOCK.acquire(blocking=False):
        return
    try:
        now = time.monotonic()
        if now - _LAST_SYNC_MONOTONIC < _SYNC_INTERVAL_SECONDS:
            return
        sync_sales_leads()
        _LAST_SYNC_MONOTONIC = time.monotonic()
    finally:
        _SYNC_LOCK.release()


def _filter_sql(
    filter_name: str,
    *,
    admin_id: int | None,
) -> tuple[str, tuple[Any, ...]]:
    selected = normalize_filter(filter_name)
    if selected in SALES_STAGES:
        return "stage=?", (selected,)
    if selected == "overdue":
        return (
            f"stage IN ({_OPEN_STAGE_SQL}) "
            "AND next_contact_at IS NOT NULL AND next_contact_at < ?",
            (_iso(),),
        )
    if selected == "mine":
        return (
            f"assigned_to=? AND stage IN ({_OPEN_STAGE_SQL})",
            (int(admin_id or 0),),
        )
    if selected == "unassigned":
        return f"assigned_to IS NULL AND stage IN ({_OPEN_STAGE_SQL})", ()
    return f"stage IN ({_OPEN_STAGE_SQL})", ()


def sales_desk_snapshot(
    *,
    filter_name: str = "open",
    admin_id: int | None = None,
    limit: int = 12,
    sync: bool = True,
) -> dict[str, Any]:
    if sync:
        try:
            _sync_if_due()
        except SalesDeskUnavailable:
            raise
        except sqlite3.Error:
            log.warning("Sales Desk source sync database failure", exc_info=True)
        except OSError:
            log.warning("Sales Desk source sync operating failure", exc_info=True)
        except RuntimeError:
            log.warning("Sales Desk source sync runtime failure", exc_info=True)
        except TypeError:
            log.warning("Sales Desk source sync type failure", exc_info=True)
        except ValueError:
            log.warning("Sales Desk source sync value failure", exc_info=True)

    selected = normalize_filter(filter_name)
    where, params = _filter_sql(selected, admin_id=admin_id)
    with db() as conn:
        ensure_schema(conn)
        rows = _rows(
            conn.execute(
                f"""
                SELECT l.*,
                       (
                           SELECT COUNT(*)
                           FROM sales_lead_notes n
                           WHERE n.lead_id=l.id
                       ) AS note_count
                FROM sales_leads l
                WHERE {where}
                ORDER BY
                    CASE
                        WHEN next_contact_at IS NOT NULL
                         AND next_contact_at < ? THEN 0
                        ELSE 1
                    END,
                    CASE stage
                        WHEN 'checkout' THEN 0
                        WHEN 'qualified' THEN 1
                        WHEN 'contacted' THEN 2
                        WHEN 'new' THEN 3
                        WHEN 'won' THEN 4
                        ELSE 5
                    END,
                    COALESCE(last_activity_at, updated_at) DESC,
                    id DESC
                LIMIT ?
                """.strip(),
                (*params, _iso(), max(1, min(int(limit), 50))),
            ).fetchall()
        )
        count_rows = _rows(
            conn.execute(
                "SELECT stage, COUNT(*) AS n FROM sales_leads GROUP BY stage"
            ).fetchall()
        )
        operational = _rowdict(
            conn.execute(
                f"""
                SELECT
                    SUM(
                        CASE
                            WHEN assigned_to IS NULL
                             AND stage IN ({_OPEN_STAGE_SQL}) THEN 1
                            ELSE 0
                        END
                    ) AS unassigned,
                    SUM(
                        CASE
                            WHEN next_contact_at IS NOT NULL
                             AND next_contact_at < ?
                             AND stage IN ({_OPEN_STAGE_SQL}) THEN 1
                            ELSE 0
                        END
                    ) AS overdue,
                    SUM(
                        CASE WHEN stage='won' THEN revenue_minor ELSE 0 END
                    ) AS won_revenue_minor
                FROM sales_leads
                """.strip(),
                (_iso(),),
            ).fetchone()
        )

    counts = {stage: 0 for stage in SALES_STAGES}
    for row in count_rows:
        counts[normalize_stage(str(row.get("stage") or "new"))] = int(
            row.get("n") or 0
        )
    return {
        "ok": True,
        "filter": selected,
        "counts": counts,
        "unassigned": int(operational.get("unassigned") or 0),
        "overdue": int(operational.get("overdue") or 0),
        "won_revenue_minor": int(operational.get("won_revenue_minor") or 0),
        "leads": rows,
    }


def get_lead(lead_id: int) -> dict[str, Any]:
    with db() as conn:
        ensure_schema(conn)
        lead = _fetch_lead(conn, int(lead_id))
        if not lead:
            raise ValueError("sales_lead_not_found")
        lead["notes"] = _rows(
            conn.execute(
                """
                SELECT id, author_id, note_text, created_at
                FROM sales_lead_notes
                WHERE lead_id=?
                ORDER BY id DESC
                LIMIT 5
                """.strip(),
                (int(lead_id),),
            ).fetchall()
        )
        lead["audit"] = _rows(
            conn.execute(
                """
                SELECT id, event_type, actor_id, details_json, created_at
                FROM sales_lead_audit
                WHERE lead_id=?
                ORDER BY id DESC
                LIMIT 10
                """.strip(),
                (int(lead_id),),
            ).fetchall()
        )
    return lead


def _claim_for_action(
    conn: Any,
    lead: dict[str, Any],
    *,
    actor_id: int,
    force: bool,
) -> dict[str, Any]:
    owner = lead.get("assigned_to")
    if owner is not None and int(owner) != int(actor_id) and not force:
        raise PermissionError("sales_lead_owned_by_another_admin")
    if owner is not None and int(owner) == int(actor_id):
        return lead

    before = _snapshot(lead)
    conn.execute(
        """
        UPDATE sales_leads
        SET assigned_to=?, updated_at=?, version=version+1
        WHERE id=? AND version=?
        """.strip(),
        (
            int(actor_id),
            _iso(),
            int(lead["id"]),
            int(lead.get("version") or 1),
        ),
    )
    if _changed_count(conn) != 1:
        current = _fetch_lead(conn, int(lead["id"]))
        current_owner = current.get("assigned_to")
        if current_owner is not None and int(current_owner) != int(actor_id):
            raise PermissionError("sales_lead_owned_by_another_admin")
        raise RuntimeError("sales_lead_concurrent_update")

    changed = _fetch_lead(conn, int(lead["id"]))
    _audit(
        conn,
        lead_id=int(lead["id"]),
        event_type="lead_assigned",
        actor_id=int(actor_id),
        before=before,
        after=_snapshot(changed),
        details={"force": bool(force), "previous_owner": owner},
    )
    return changed


def claim_lead(*, lead_id: int, actor_id: int, force: bool = False) -> dict[str, Any]:
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = _fetch_lead(conn, int(lead_id))
            if not lead:
                raise ValueError("sales_lead_not_found")
            _claim_for_action(
                conn,
                lead,
                actor_id=int(actor_id),
                force=bool(force),
            )
    return get_lead(int(lead_id))


def _versioned_update(
    conn: Any,
    *,
    lead_id: int,
    version: int,
    sql: str,
    params: tuple[Any, ...],
) -> dict[str, Any]:
    conn.execute(sql, (*params, int(lead_id), int(version)))
    if _changed_count(conn) != 1:
        raise RuntimeError("sales_lead_concurrent_update")
    return _fetch_lead(conn, int(lead_id))


def set_lead_stage(
    *,
    lead_id: int,
    target_stage: str,
    actor_id: int,
    force_owner: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    target = normalize_stage(target_stage)
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = _fetch_lead(conn, int(lead_id))
            if not lead:
                raise ValueError("sales_lead_not_found")
            lead = _claim_for_action(
                conn,
                lead,
                actor_id=int(actor_id),
                force=bool(force_owner),
            )
            current = normalize_stage(str(lead.get("stage") or "new"))
            if current == target:
                return get_lead(int(lead_id))
            assert_transition(current, target)

            before = _snapshot(lead)
            now = _iso()
            changed = _versioned_update(
                conn,
                lead_id=int(lead_id),
                version=int(lead.get("version") or 1),
                sql="""
                    UPDATE sales_leads
                    SET stage=?, stage_source='manual', last_contact_at=?,
                        next_contact_at=CASE
                            WHEN ? IN ('won','lost') THEN NULL
                            ELSE next_contact_at
                        END,
                        closed_reason=CASE
                            WHEN ?='lost' THEN ?
                            WHEN ?='won' THEN NULL
                            ELSE closed_reason
                        END,
                        updated_at=?, version=version+1
                    WHERE id=? AND version=?
                """.strip(),
                params=(
                    target,
                    now,
                    target,
                    target,
                    _clean_text(reason, limit=500),
                    target,
                    now,
                ),
            )
            _audit(
                conn,
                lead_id=int(lead_id),
                event_type="stage_changed",
                actor_id=int(actor_id),
                before=before,
                after=_snapshot(changed),
                details={
                    "from": current,
                    "to": target,
                    "reason": _clean_text(reason, limit=500),
                },
            )
    return get_lead(int(lead_id))


def set_next_contact(
    *,
    lead_id: int,
    days: int | None,
    actor_id: int,
    force_owner: bool = False,
) -> dict[str, Any]:
    if days is not None and int(days) not in {1, 3, 7}:
        raise ValueError("invalid_sales_follow_up_days")

    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = _fetch_lead(conn, int(lead_id))
            if not lead:
                raise ValueError("sales_lead_not_found")
            lead = _claim_for_action(
                conn,
                lead,
                actor_id=int(actor_id),
                force=bool(force_owner),
            )
            if normalize_stage(str(lead.get("stage") or "new")) in {"won", "lost"}:
                raise ValueError("sales_follow_up_closed_lead")

            target = (
                None
                if days is None
                else _iso(_utc_now() + timedelta(days=int(days)))
            )
            if (lead.get("next_contact_at") or None) == target:
                return get_lead(int(lead_id))

            before = _snapshot(lead)
            changed = _versioned_update(
                conn,
                lead_id=int(lead_id),
                version=int(lead.get("version") or 1),
                sql="""
                    UPDATE sales_leads
                    SET next_contact_at=?, updated_at=?, version=version+1
                    WHERE id=? AND version=?
                """.strip(),
                params=(target, _iso()),
            )
            _audit(
                conn,
                lead_id=int(lead_id),
                event_type="follow_up_changed",
                actor_id=int(actor_id),
                before=before,
                after=_snapshot(changed),
                details={"days": days},
            )
    return get_lead(int(lead_id))


def add_note(
    *,
    lead_id: int,
    actor_id: int,
    note_text: str,
    force_owner: bool = False,
) -> dict[str, Any]:
    note = sanitize_note(note_text)
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = _fetch_lead(conn, int(lead_id))
            if not lead:
                raise ValueError("sales_lead_not_found")
            lead = _claim_for_action(
                conn,
                lead,
                actor_id=int(actor_id),
                force=bool(force_owner),
            )
            before = _snapshot(lead)
            now = _iso()
            conn.execute(
                """
                INSERT INTO sales_lead_notes(
                    lead_id, author_id, note_text, created_at
                ) VALUES(?,?,?,?)
                """.strip(),
                (int(lead_id), int(actor_id), note, now),
            )
            changed = _versioned_update(
                conn,
                lead_id=int(lead_id),
                version=int(lead.get("version") or 1),
                sql="""
                    UPDATE sales_leads
                    SET last_contact_at=?, updated_at=?, version=version+1
                    WHERE id=? AND version=?
                """.strip(),
                params=(now, now),
            )
            _audit(
                conn,
                lead_id=int(lead_id),
                event_type="note_added",
                actor_id=int(actor_id),
                before=before,
                after=_snapshot(changed),
                details={"note_preview": note[:120]},
            )
    return get_lead(int(lead_id))


def stage_title(stage: str | None) -> str:
    return _STAGE_TITLES[normalize_stage(stage)]


def format_money(amount_minor: int, currency: str = "RUB") -> str:
    amount = max(0, int(amount_minor or 0))
    normalized_currency = str(currency or "RUB").upper()
    if normalized_currency == "RUB":
        return f"{amount / 100:.2f} ₽"
    return f"{amount / 100:.2f} {normalized_currency}".strip()


def format_sales_overview(snapshot: dict[str, Any]) -> str:
    counts = dict(snapshot.get("counts") or {})
    return "\n".join(
        [
            "🧑‍💼 Sales Desk",
            "",
            "Операционная очередь отдела продаж. Пользовательская воронка и платежи не изменяются.",
            "",
            f"Новые: {int(counts.get('new') or 0)}",
            f"Связались: {int(counts.get('contacted') or 0)}",
            f"Заинтересованы: {int(counts.get('qualified') or 0)}",
            f"Начали оплату: {int(counts.get('checkout') or 0)}",
            f"Оплатили: {int(counts.get('won') or 0)}",
            f"Отказы: {int(counts.get('lost') or 0)}",
            "",
            f"Без ответственного: {int(snapshot.get('unassigned') or 0)}",
            f"Просрочен следующий контакт: {int(snapshot.get('overdue') or 0)}",
            "Выручка выигранных лидов: "
            f"{format_money(int(snapshot.get('won_revenue_minor') or 0))}",
        ]
    )


def format_lead_card(lead: dict[str, Any]) -> str:
    owner = lead.get("assigned_to")
    lines = [
        f"🧑‍💼 Лид #{int(lead.get('id') or 0)}",
        "",
        f"Клиент: {str(lead.get('display_name') or 'Пользователь')}",
        f"User ID: {lead.get('user_id') or '—'}",
        f"Этап: {stage_title(str(lead.get('stage') or 'new'))}",
        f"Ответственный: {owner if owner is not None else 'не назначен'}",
        f"Источник: {str(lead.get('source') or 'organic')}",
        f"Кампания: {str(lead.get('campaign') or '—')}",
        f"Креатив: {str(lead.get('creative') or '—')}",
        "Выручка: "
        f"{format_money(int(lead.get('revenue_minor') or 0), str(lead.get('currency') or 'RUB'))}",
        f"Последняя активность: {str(lead.get('last_activity_at') or '—')}",
        f"Следующий контакт: {str(lead.get('next_contact_at') or 'не назначен')}",
    ]
    notes = list(lead.get("notes") or [])
    if notes:
        lines.extend(["", "Последние заметки:"])
        for note in notes[:3]:
            lines.append(
                f"• {str(note.get('note_text') or '')[:180]} "
                f"— admin {note.get('author_id')}"
            )
    return "\n".join(lines)


def format_lead_history(lead: dict[str, Any]) -> str:
    lines = [f"🧾 История лида #{int(lead.get('id') or 0)}", ""]
    audit = list(lead.get("audit") or [])
    if not audit:
        lines.append("Событий пока нет.")
        return "\n".join(lines)
    for event in audit[:10]:
        lines.append(
            f"• {str(event.get('created_at') or '—')} · "
            f"{str(event.get('event_type') or 'event')} · "
            f"actor {event.get('actor_id')}"
        )
    return "\n".join(lines)
