from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from services.db import db, tx
from services.migrations._helpers import table_exists
from services.sales_desk_core import (
    OPEN_STAGES,
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
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _table_columns(conn: Any, table: str) -> set[str]:
    if os.getenv("METRO_DB_ENGINE", "").strip().lower() == "postgres":
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=?
            """.strip(),
            (str(table),),
        ).fetchall()
        return {
            str(_rowdict(row).get("column_name") or "")
            for row in rows
            if str(_rowdict(row).get("column_name") or "")
        }
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


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
            lead_id, event_type, actor_id, before_json, after_json, details_json, created_at
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


def _event_rows(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not table_exists(conn, "events"):
        return []
    event_columns = _table_columns(conn, "events")
    if not {"user_id", "name"}.issubset(event_columns):
        return []

    select = ["e.user_id", "e.name"]
    for column in ("created_at", "meta", "payload"):
        select.append(f"e.{column}" if column in event_columns else f"NULL AS {column}")

    join = ""
    if table_exists(conn, "users"):
        user_columns = _table_columns(conn, "users")
        join = " LEFT JOIN users u ON u.user_id=e.user_id"
        select.append("u.username" if "username" in user_columns else "NULL AS username")
        select.append("u.first_name" if "first_name" in user_columns else "NULL AS first_name")
    else:
        select.extend(["NULL AS username", "NULL AS first_name"])

    placeholders = ",".join("?" for _ in _SALES_EVENT_NAMES)
    order = "e.created_at" if "created_at" in event_columns else "e.user_id"
    return _rows(
        conn.execute(
            f"""
            SELECT {', '.join(select)}
            FROM events e{join}
            WHERE e.user_id IS NOT NULL AND e.name IN ({placeholders})
            ORDER BY {order} ASC
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

    status_column = "provider_status" if "provider_status" in columns else ("status" if "status" in columns else "")
    amount_column = "amount" if "amount" in columns else ("amount_minor" if "amount_minor" in columns else "")
    currency_column = "currency" if "currency" in columns else ""
    created_column = "created_at" if "created_at" in columns else ("paid_at" if "paid_at" in columns else "")

    conditions = ["user_id IS NOT NULL"]
    params: list[Any] = []
    if status_column:
        conditions.append(f"COALESCE({status_column}, 'succeeded') IN ('succeeded','paid','success','captured')")

    amount_expression = f"COALESCE({amount_column}, 0)" if amount_column else "0"
    currency_expression = f"COALESCE(MAX({currency_column}), 'RUB')" if currency_column else "'RUB'"
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
            """.strip(),
            tuple(params),
        ).fetchall()
    )


def _candidate_map(conn: Any, *, limit: int) -> dict[int, dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    for row in _event_rows(conn, limit=limit):
        try:
            user_id = int(row.get("user_id"))
        except (TypeError, ValueError):
            continue
        item = candidates.setdefault(
            user_id,
            {
                "user_id": user_id,
                "event_names": [],
                "first_name": row.get("first_name"),
                "username": row.get("username"),
                "source": "organic",
                "campaign": "",
                "creative": "",
                "first_activity_at": row.get("created_at") or _iso(),
                "last_activity_at": row.get("created_at") or _iso(),
                "revenue_minor": 0,
                "currency": "RUB",
            },
        )
        item["event_names"].append(str(row.get("name") or ""))
        if row.get("first_name"):
            item["first_name"] = row.get("first_name")
        if row.get("username"):
            item["username"] = row.get("username")
        created_at = str(row.get("created_at") or "")
        if created_at:
            if created_at < str(item.get("first_activity_at") or created_at):
                item["first_activity_at"] = created_at
            if created_at > str(item.get("last_activity_at") or ""):
                item["last_activity_at"] = created_at
        attribution = extract_attribution(row.get("meta"), row.get("payload"))
        if attribution["source"] != "organic" or item["source"] == "organic":
            item.update(attribution)

    for payment in _payment_rows(conn):
        try:
            user_id = int(payment.get("user_id"))
        except (TypeError, ValueError):
            continue
        item = candidates.setdefault(
            user_id,
            {
                "user_id": user_id,
                "event_names": [],
                "first_name": None,
                "username": None,
                "source": "organic",
                "campaign": "",
                "creative": "",
                "first_activity_at": payment.get("paid_at") or _iso(),
                "last_activity_at": payment.get("paid_at") or _iso(),
                "revenue_minor": 0,
                "currency": "RUB",
            },
        )
        item["event_names"].append("payment_success")
        item["revenue_minor"] = max(0, int(payment.get("revenue_minor") or 0))
        item["currency"] = str(payment.get("currency") or "RUB")[:12]
        if payment.get("paid_at"):
            item["last_activity_at"] = max(
                str(item.get("last_activity_at") or ""),
                str(payment.get("paid_at") or ""),
            )
    return candidates


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
                    conn.execute("SELECT * FROM sales_leads WHERE lead_key=? LIMIT 1", (key,)).fetchone()
                )
                incoming_stage = stage_from_event_names(candidate["event_names"])
                display_name = compact_display_name(
                    first_name=candidate.get("first_name"),
                    username=candidate.get("username"),
                    user_id=user_id,
                )
                if not existing:
                    created_at = str(candidate.get("first_activity_at") or _iso())
                    conn.execute(
                        """
                        INSERT INTO sales_leads(
                            lead_key, user_id, display_name, username, source, campaign, creative,
                            stage, stage_source, last_activity_at, revenue_minor, currency,
                            created_at, updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """.strip(),
                        (
                            key,
                            user_id,
                            display_name,
                            str(candidate.get("username") or "")[:100] or None,
                            str(candidate.get("source") or "organic")[:100],
                            str(candidate.get("campaign") or "")[:160] or None,
                            str(candidate.get("creative") or "")[:160] or None,
                            incoming_stage,
                            "auto",
                            str(candidate.get("last_activity_at") or created_at),
                            max(0, int(candidate.get("revenue_minor") or 0)),
                            str(candidate.get("currency") or "RUB")[:12],
                            created_at,
                            _iso(),
                        ),
                    )
                    created = _rowdict(
                        conn.execute("SELECT * FROM sales_leads WHERE lead_key=? LIMIT 1", (key,)).fetchone()
                    )
                    _audit(
                        conn,
                        lead_id=int(created["id"]),
                        event_type="lead_discovered",
                        actor_id=0,
                        before=None,
                        after=_snapshot(created),
                        details={"events": sorted(set(candidate["event_names"]))},
                    )
                    inserted += 1
                    continue

                before = _snapshot(existing)
                target_stage = str(existing.get("stage") or "new")
                auto_promoted = should_auto_advance(
                    target_stage,
                    incoming_stage,
                    stage_source=str(existing.get("stage_source") or "auto"),
                )
                if auto_promoted:
                    target_stage = incoming_stage
                conn.execute(
                    """
                    UPDATE sales_leads
                    SET display_name=?, username=?, source=?, campaign=?, creative=?,
                        stage=?, stage_source=?, last_activity_at=?, revenue_minor=?, currency=?,
                        updated_at=?, version=version+1
                    WHERE id=?
                    """.strip(),
                    (
                        display_name,
                        str(candidate.get("username") or existing.get("username") or "")[:100] or None,
                        str(candidate.get("source") or existing.get("source") or "organic")[:100],
                        str(candidate.get("campaign") or existing.get("campaign") or "")[:160] or None,
                        str(candidate.get("creative") or existing.get("creative") or "")[:160] or None,
                        target_stage,
                        "auto" if auto_promoted else str(existing.get("stage_source") or "auto"),
                        max(
                            str(existing.get("last_activity_at") or ""),
                            str(candidate.get("last_activity_at") or ""),
                        ) or None,
                        max(
                            int(existing.get("revenue_minor") or 0),
                            int(candidate.get("revenue_minor") or 0),
                        ),
                        str(candidate.get("currency") or existing.get("currency") or "RUB")[:12],
                        _iso(),
                        int(existing["id"]),
                    ),
                )
                after = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=?", (int(existing["id"]),)).fetchone())
                if auto_promoted:
                    _audit(
                        conn,
                        lead_id=int(existing["id"]),
                        event_type="stage_auto_advanced",
                        actor_id=0,
                        before=before,
                        after=_snapshot(after),
                        details={"incoming_stage": incoming_stage},
                    )
                    promoted += 1
                updated += 1
    return {"inserted": inserted, "updated": updated, "promoted": promoted}


def _filter_sql(filter_name: str, *, admin_id: int | None) -> tuple[str, tuple[Any, ...]]:
    selected = normalize_filter(filter_name)
    if selected in SALES_STAGES:
        return "stage=?", (selected,)
    if selected == "overdue":
        return "stage IN ('new','contacted','qualified','checkout') AND next_contact_at IS NOT NULL AND next_contact_at < ?", (_iso(),)
    if selected == "mine":
        return "assigned_to=? AND stage IN ('new','contacted','qualified','checkout')", (int(admin_id or 0),)
    if selected == "unassigned":
        return "assigned_to IS NULL AND stage IN ('new','contacted','qualified','checkout')", ()
    return "stage IN ('new','contacted','qualified','checkout')", ()


def sales_desk_snapshot(
    *,
    filter_name: str = "open",
    admin_id: int | None = None,
    limit: int = 12,
    sync: bool = True,
) -> dict[str, Any]:
    if sync:
        try:
            sync_sales_leads()
        except SalesDeskUnavailable:
            raise
        except (sqlite3.Error, OSError, RuntimeError, TypeError, ValueError):
            log.warning("Sales Desk source sync degraded", exc_info=True)

    selected = normalize_filter(filter_name)
    where, params = _filter_sql(selected, admin_id=admin_id)
    with db() as conn:
        ensure_schema(conn)
        rows = _rows(
            conn.execute(
                f"""
                SELECT l.*,
                       (SELECT COUNT(*) FROM sales_lead_notes n WHERE n.lead_id=l.id) AS note_count
                FROM sales_leads l
                WHERE {where}
                ORDER BY
                    CASE WHEN next_contact_at IS NOT NULL AND next_contact_at < ? THEN 0 ELSE 1 END,
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
        count_rows = _rows(conn.execute("SELECT stage, COUNT(*) AS n FROM sales_leads GROUP BY stage").fetchall())
        operational = _rowdict(
            conn.execute(
                """
                SELECT
                    SUM(CASE WHEN assigned_to IS NULL AND stage IN ('new','contacted','qualified','checkout') THEN 1 ELSE 0 END) AS unassigned,
                    SUM(CASE WHEN next_contact_at IS NOT NULL AND next_contact_at < ? AND stage IN ('new','contacted','qualified','checkout') THEN 1 ELSE 0 END) AS overdue,
                    SUM(CASE WHEN stage='won' THEN revenue_minor ELSE 0 END) AS won_revenue_minor
                FROM sales_leads
                """.strip(),
                (_iso(),),
            ).fetchone()
        )
    counts = {stage: 0 for stage in SALES_STAGES}
    for row in count_rows:
        counts[normalize_stage(str(row.get("stage") or "new"))] = int(row.get("n") or 0)
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
        lead = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=? LIMIT 1", (int(lead_id),)).fetchone())
        if not lead:
            raise ValueError("sales_lead_not_found")
        lead["notes"] = _rows(
            conn.execute(
                "SELECT id, author_id, note_text, created_at FROM sales_lead_notes WHERE lead_id=? ORDER BY id DESC LIMIT 5",
                (int(lead_id),),
            ).fetchall()
        )
        lead["audit"] = _rows(
            conn.execute(
                "SELECT id, event_type, actor_id, details_json, created_at FROM sales_lead_audit WHERE lead_id=? ORDER BY id DESC LIMIT 10",
                (int(lead_id),),
            ).fetchall()
        )
    return lead


def _owned_or_claimed(
    conn: Any,
    lead: dict[str, Any],
    *,
    actor_id: int,
    force: bool,
) -> dict[str, Any]:
    owner = lead.get("assigned_to")
    if owner is not None and int(owner) != int(actor_id) and not force:
        raise PermissionError("sales_lead_owned_by_another_admin")
    if owner is None or (force and int(owner or 0) != int(actor_id)):
        conn.execute(
            "UPDATE sales_leads SET assigned_to=?, updated_at=?, version=version+1 WHERE id=?",
            (int(actor_id), _iso(), int(lead["id"])),
        )
        lead = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=?", (int(lead["id"]),)).fetchone())
    return lead


def claim_lead(*, lead_id: int, actor_id: int, force: bool = False) -> dict[str, Any]:
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=? LIMIT 1", (int(lead_id),)).fetchone())
            if not lead:
                raise ValueError("sales_lead_not_found")
            before = _snapshot(lead)
            lead = _owned_or_claimed(conn, lead, actor_id=int(actor_id), force=bool(force))
            after = _snapshot(lead)
            if before != after:
                _audit(
                    conn,
                    lead_id=int(lead_id),
                    event_type="lead_assigned",
                    actor_id=int(actor_id),
                    before=before,
                    after=after,
                    details={"force": bool(force)},
                )
    return get_lead(int(lead_id))


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
            lead = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=? LIMIT 1", (int(lead_id),)).fetchone())
            if not lead:
                raise ValueError("sales_lead_not_found")
            lead = _owned_or_claimed(conn, lead, actor_id=int(actor_id), force=bool(force_owner))
            current = normalize_stage(str(lead.get("stage") or "new"))
            if current == target:
                return lead
            assert_transition(current, target)
            before = _snapshot(lead)
            now = _iso()
            conn.execute(
                """
                UPDATE sales_leads
                SET stage=?, stage_source='manual', last_contact_at=?,
                    next_contact_at=CASE WHEN ? IN ('won','lost') THEN NULL ELSE next_contact_at END,
                    closed_reason=CASE WHEN ?='lost' THEN ? WHEN ?='won' THEN NULL ELSE closed_reason END,
                    updated_at=?, version=version+1
                WHERE id=?
                """.strip(),
                (target, now, target, target, str(reason or "")[:500] or None, target, now, int(lead_id)),
            )
            changed = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=?", (int(lead_id),)).fetchone())
            _audit(
                conn,
                lead_id=int(lead_id),
                event_type="stage_changed",
                actor_id=int(actor_id),
                before=before,
                after=_snapshot(changed),
                details={"from": current, "to": target, "reason": str(reason or "")[:500]},
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
            lead = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=? LIMIT 1", (int(lead_id),)).fetchone())
            if not lead:
                raise ValueError("sales_lead_not_found")
            lead = _owned_or_claimed(conn, lead, actor_id=int(actor_id), force=bool(force_owner))
            before = _snapshot(lead)
            target = None if days is None else _iso(_utc_now() + timedelta(days=int(days)))
            conn.execute(
                "UPDATE sales_leads SET next_contact_at=?, updated_at=?, version=version+1 WHERE id=?",
                (target, _iso(), int(lead_id)),
            )
            changed = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=?", (int(lead_id),)).fetchone())
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
            lead = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=? LIMIT 1", (int(lead_id),)).fetchone())
            if not lead:
                raise ValueError("sales_lead_not_found")
            lead = _owned_or_claimed(conn, lead, actor_id=int(actor_id), force=bool(force_owner))
            before = _snapshot(lead)
            conn.execute(
                "INSERT INTO sales_lead_notes(lead_id, author_id, note_text, created_at) VALUES(?,?,?,?)",
                (int(lead_id), int(actor_id), note, _iso()),
            )
            conn.execute(
                "UPDATE sales_leads SET last_contact_at=?, updated_at=?, version=version+1 WHERE id=?",
                (_iso(), _iso(), int(lead_id)),
            )
            changed = _rowdict(conn.execute("SELECT * FROM sales_leads WHERE id=?", (int(lead_id),)).fetchone())
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
    if str(currency or "RUB").upper() == "RUB":
        return f"{amount / 100:.2f} ₽"
    return f"{amount / 100:.2f} {str(currency or '').upper()}".strip()


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
            f"Выручка выигранных лидов: {format_money(int(snapshot.get('won_revenue_minor') or 0))}",
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
        f"Выручка: {format_money(int(lead.get('revenue_minor') or 0), str(lead.get('currency') or 'RUB'))}",
        f"Последняя активность: {str(lead.get('last_activity_at') or '—')}",
        f"Следующий контакт: {str(lead.get('next_contact_at') or 'не назначен')}",
    ]
    notes = list(lead.get("notes") or [])
    if notes:
        lines.extend(["", "Последние заметки:"])
        for note in notes[:3]:
            lines.append(
                f"• {str(note.get('note_text') or '')[:180]} — admin {note.get('author_id')}"
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
            f"• {str(event.get('created_at') or '—')} · {str(event.get('event_type') or 'event')} · actor {event.get('actor_id')}"
        )
    return "\n".join(lines)
