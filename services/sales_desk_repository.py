from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from services.db import db, tx
from services.sales_desk_core import (
    SALES_STAGES,
    assert_transition,
    normalize_filter,
    normalize_stage,
    sanitize_note,
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
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_now() -> str:
    return utc_now().isoformat()


def _sales_filter_parameters(
    filter_name: str,
    *,
    admin_id: int | None,
    now_iso: str,
) -> tuple[Any, ...]:
    selected = normalize_filter(filter_name)
    stage = selected if selected in SALES_STAGES else ""
    return (
        stage,
        stage,
        selected,
        now_iso,
        selected,
        int(admin_id or 0),
        selected,
        selected,
    )


def _revenue_map(conn: Any, user_ids: list[int] | None = None) -> dict[int, dict[str, int]]:
    sql = "SELECT user_id, currency, amount_units FROM sales_lead_revenue"
    params: tuple[Any, ...] = ()
    if user_ids:
        placeholders = ",".join("?" for _ in user_ids)
        sql += f" WHERE user_id IN ({placeholders})"
        params = tuple(int(item) for item in user_ids)
    result: dict[int, dict[str, int]] = {}
    for row in rows(conn.execute(sql, params).fetchall()):
        result.setdefault(int(row.get("user_id") or 0), {})[str(row.get("currency") or "RUB").upper()] = int(row.get("amount_units") or 0)
    return result


def read_sales_snapshot(
    *,
    filter_name: str,
    admin_id: int | None,
    limit: int,
    now_iso: str,
) -> dict[str, Any]:
    selected = normalize_filter(filter_name)
    filter_params = _sales_filter_parameters(
        selected,
        admin_id=admin_id,
        now_iso=now_iso,
    )
    with db() as conn:
        ensure_schema(conn)
        lead_rows = rows(
            conn.execute(
                """
                SELECT l.*,
                       (
                           SELECT COUNT(*)
                           FROM sales_lead_notes n
                           WHERE n.lead_id=l.id
                       ) AS note_count
                FROM sales_leads l
                WHERE
                    (? <> '' AND l.stage=?)
                    OR (
                        ?='overdue'
                        AND l.stage IN ('new','contacted','qualified','checkout')
                        AND l.next_contact_at IS NOT NULL
                        AND l.next_contact_at < ?
                    )
                    OR (
                        ?='mine'
                        AND l.assigned_to=?
                        AND l.stage IN ('new','contacted','qualified','checkout')
                    )
                    OR (
                        ?='unassigned'
                        AND l.assigned_to IS NULL
                        AND l.stage IN ('new','contacted','qualified','checkout')
                    )
                    OR (
                        ?='open'
                        AND l.stage IN ('new','contacted','qualified','checkout')
                    )
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
                (*filter_params, now_iso, max(1, min(int(limit), 50))),
            ).fetchall()
        )
        count_rows = rows(
            conn.execute(
                "SELECT stage, COUNT(*) AS n FROM sales_leads GROUP BY stage"
            ).fetchall()
        )
        operational = rowdict(
            conn.execute(
                """
                SELECT
                    SUM(
                        CASE
                            WHEN assigned_to IS NULL
                             AND stage IN ('new','contacted','qualified','checkout') THEN 1
                            ELSE 0
                        END
                    ) AS unassigned,
                    SUM(
                        CASE
                            WHEN next_contact_at IS NOT NULL
                             AND next_contact_at < ?
                             AND stage IN ('new','contacted','qualified','checkout') THEN 1
                            ELSE 0
                        END
                    ) AS overdue,
                    SUM(CASE WHEN stage='won' THEN revenue_minor ELSE 0 END) AS won_revenue_minor
                FROM sales_leads
                """.strip(),
                (now_iso,),
            ).fetchone()
        )
        lead_revenue = _revenue_map(conn, [int(item.get("user_id") or 0) for item in lead_rows])
        won_currency_rows = rows(
            conn.execute(
                """
                SELECT r.currency, SUM(r.amount_units) AS amount_units
                FROM sales_lead_revenue r
                JOIN sales_leads l ON l.user_id=r.user_id
                WHERE l.stage='won'
                GROUP BY r.currency
                """
            ).fetchall()
        )

    for lead in lead_rows:
        lead["revenue_by_currency"] = lead_revenue.get(int(lead.get("user_id") or 0), {})
    won_revenue_by_currency = {str(item.get("currency") or "RUB"): int(item.get("amount_units") or 0) for item in won_currency_rows}
    counts = {stage: 0 for stage in SALES_STAGES}
    for item in count_rows:
        counts[normalize_stage(str(item.get("stage") or "new"))] = int(
            item.get("n") or 0
        )
    return {
        "ok": True,
        "filter": selected,
        "counts": counts,
        "unassigned": int(operational.get("unassigned") or 0),
        "overdue": int(operational.get("overdue") or 0),
        "won_revenue_minor": int(won_revenue_by_currency.get("RUB") or 0),
        "won_revenue_by_currency": won_revenue_by_currency,
        "leads": lead_rows,
    }


def get_lead(lead_id: int) -> dict[str, Any]:
    with db() as conn:
        ensure_schema(conn)
        lead = fetch_lead(conn, int(lead_id))
        if not lead:
            raise ValueError("sales_lead_not_found")
        lead["revenue_by_currency"] = _revenue_map(conn, [int(lead.get("user_id") or 0)]).get(int(lead.get("user_id") or 0), {})
        lead["notes"] = rows(
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
        lead["audit"] = rows(
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
    now_iso: str,
) -> dict[str, Any]:
    owner = lead.get("assigned_to")
    if owner is not None and int(owner) != int(actor_id) and not force:
        raise PermissionError("sales_lead_owned_by_another_admin")
    if owner is not None and int(owner) == int(actor_id):
        return lead

    before = lead_snapshot(lead)
    conn.execute(
        """
        UPDATE sales_leads
        SET assigned_to=?, updated_at=?, version=version+1
        WHERE id=? AND version=?
        """.strip(),
        (
            int(actor_id),
            now_iso,
            int(lead["id"]),
            int(lead.get("version") or 1),
        ),
    )
    if changed_count(conn) != 1:
        current = fetch_lead(conn, int(lead["id"]))
        current_owner = current.get("assigned_to")
        if current_owner is not None and int(current_owner) != int(actor_id):
            raise PermissionError("sales_lead_owned_by_another_admin")
        raise RuntimeError("sales_lead_concurrent_update")

    changed = fetch_lead(conn, int(lead["id"]))
    audit(
        conn,
        lead_id=int(lead["id"]),
        event_type="lead_assigned",
        actor_id=int(actor_id),
        before=before,
        after=lead_snapshot(changed),
        details={"force": bool(force), "previous_owner": owner},
        created_at=now_iso,
    )
    return changed


def claim_lead(*, lead_id: int, actor_id: int, force: bool = False) -> dict[str, Any]:
    now_iso = iso_now()
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = fetch_lead(conn, int(lead_id))
            if not lead:
                raise ValueError("sales_lead_not_found")
            _claim_for_action(
                conn,
                lead,
                actor_id=int(actor_id),
                force=bool(force),
                now_iso=now_iso,
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
    if changed_count(conn) != 1:
        raise RuntimeError("sales_lead_concurrent_update")
    return fetch_lead(conn, int(lead_id))


def set_lead_stage(
    *,
    lead_id: int,
    target_stage: str,
    actor_id: int,
    force_owner: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    target = normalize_stage(target_stage)
    now_iso = iso_now()
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = fetch_lead(conn, int(lead_id))
            if not lead:
                raise ValueError("sales_lead_not_found")
            lead = _claim_for_action(
                conn,
                lead,
                actor_id=int(actor_id),
                force=bool(force_owner),
                now_iso=now_iso,
            )
            current = normalize_stage(str(lead.get("stage") or "new"))
            if current != target:
                assert_transition(current, target)
                before = lead_snapshot(lead)
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
                        now_iso,
                        target,
                        target,
                        clean_text(reason, limit=500),
                        target,
                        now_iso,
                    ),
                )
                audit(
                    conn,
                    lead_id=int(lead_id),
                    event_type="stage_changed",
                    actor_id=int(actor_id),
                    before=before,
                    after=lead_snapshot(changed),
                    details={
                        "from": current,
                        "to": target,
                        "reason": clean_text(reason, limit=500),
                    },
                    created_at=now_iso,
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

    now = utc_now()
    now_iso = now.isoformat()
    target = None if days is None else (now + timedelta(days=int(days))).isoformat()
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = fetch_lead(conn, int(lead_id))
            if not lead:
                raise ValueError("sales_lead_not_found")
            lead = _claim_for_action(
                conn,
                lead,
                actor_id=int(actor_id),
                force=bool(force_owner),
                now_iso=now_iso,
            )
            if normalize_stage(str(lead.get("stage") or "new")) in {"won", "lost"}:
                raise ValueError("sales_follow_up_closed_lead")
            if (lead.get("next_contact_at") or None) != target:
                before = lead_snapshot(lead)
                changed = _versioned_update(
                    conn,
                    lead_id=int(lead_id),
                    version=int(lead.get("version") or 1),
                    sql="""
                        UPDATE sales_leads
                        SET next_contact_at=?, updated_at=?, version=version+1
                        WHERE id=? AND version=?
                    """.strip(),
                    params=(target, now_iso),
                )
                audit(
                    conn,
                    lead_id=int(lead_id),
                    event_type="follow_up_changed",
                    actor_id=int(actor_id),
                    before=before,
                    after=lead_snapshot(changed),
                    details={"days": days},
                    created_at=now_iso,
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
    now_iso = iso_now()
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = fetch_lead(conn, int(lead_id))
            if not lead:
                raise ValueError("sales_lead_not_found")
            lead = _claim_for_action(
                conn,
                lead,
                actor_id=int(actor_id),
                force=bool(force_owner),
                now_iso=now_iso,
            )
            before = lead_snapshot(lead)
            conn.execute(
                """
                INSERT INTO sales_lead_notes(
                    lead_id, author_id, note_text, created_at
                ) VALUES(?,?,?,?)
                """.strip(),
                (int(lead_id), int(actor_id), note, now_iso),
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
                params=(now_iso, now_iso),
            )
            audit(
                conn,
                lead_id=int(lead_id),
                event_type="note_added",
                actor_id=int(actor_id),
                before=before,
                after=lead_snapshot(changed),
                details={"note_preview": note[:120]},
                created_at=now_iso,
            )
    return get_lead(int(lead_id))
