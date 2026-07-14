from __future__ import annotations

import uuid
from typing import Any

from services.db import db, tx
from services.migrations._helpers import table_exists
from services.sales_desk_db import (
    audit,
    changed_count,
    ensure_schema,
    fetch_lead,
    lead_snapshot,
    rowdict,
)
from services.sales_desk_repository import claim_lead, iso_now


def sanitize_sales_message(value: str, *, max_length: int = 3500) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("sales_message_empty")
    return text[: max(1, int(max_length))]


def _telegram_target(conn: Any, lead: dict[str, Any]) -> int:
    user_id = int(lead.get("user_id") or 0)
    account_id = int(lead.get("account_id") or 0)

    if table_exists(conn, "account_channel_identities"):
        row = None
        if account_id > 0:
            row = conn.execute(
                """
                SELECT external_user_id
                FROM account_channel_identities
                WHERE account_id=? AND platform='telegram'
                LIMIT 1
                """.strip(),
                (account_id,),
            ).fetchone()
        if row is None and user_id > 0:
            row = conn.execute(
                """
                SELECT external_user_id
                FROM account_channel_identities
                WHERE account_id=? AND platform='telegram'
                LIMIT 1
                """.strip(),
                (user_id,),
            ).fetchone()
        if row is None and user_id > 0:
            row = conn.execute(
                """
                SELECT external_user_id
                FROM account_channel_identities
                WHERE external_user_id=? AND platform='telegram'
                LIMIT 1
                """.strip(),
                (str(user_id),),
            ).fetchone()
        if row is not None:
            external = str(rowdict(row).get("external_user_id") or "").strip()
            if external.isdigit() and int(external) > 0:
                return int(external)

        linked = None
        if account_id > 0:
            linked = conn.execute(
                """
                SELECT 1
                FROM account_channel_identities
                WHERE account_id=?
                LIMIT 1
                """.strip(),
                (account_id,),
            ).fetchone()
        if linked is None and user_id > 0:
            linked = conn.execute(
                """
                SELECT 1
                FROM account_channel_identities
                WHERE account_id=?
                LIMIT 1
                """.strip(),
                (user_id,),
            ).fetchone()
        if linked is not None:
            raise ValueError("sales_telegram_identity_missing")

    if user_id <= 0:
        raise ValueError("sales_telegram_identity_missing")
    return user_id


def prepare_sales_message(
    *,
    lead_id: int,
    actor_id: int,
    message_text: str,
    force_owner: bool = False,
) -> dict[str, Any]:
    text = sanitize_sales_message(message_text)
    claim_lead(
        lead_id=int(lead_id),
        actor_id=int(actor_id),
        force=bool(force_owner),
    )
    now_iso = iso_now()
    key = f"sales:{uuid.uuid4().hex}"

    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            lead = fetch_lead(conn, int(lead_id))
            if not lead:
                raise ValueError("sales_lead_not_found")
            if int(lead.get("assigned_to") or 0) != int(actor_id):
                raise PermissionError("sales_lead_owned_by_another_admin")
            chat_id = _telegram_target(conn, lead)
            conn.execute(
                """
                INSERT INTO sales_outbound_messages(
                    idempotency_key, lead_id, actor_id, platform,
                    external_user_id, message_text, status,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """.strip(),
                (
                    key,
                    int(lead_id),
                    int(actor_id),
                    "telegram",
                    str(chat_id),
                    text,
                    "prepared",
                    now_iso,
                    now_iso,
                ),
            )
            outbox = rowdict(
                conn.execute(
                    """
                    SELECT *
                    FROM sales_outbound_messages
                    WHERE idempotency_key=?
                    LIMIT 1
                    """.strip(),
                    (key,),
                ).fetchone()
            )
            if not outbox:
                raise RuntimeError("sales_outbound_prepare_failed")
            audit(
                conn,
                lead_id=int(lead_id),
                event_type="outbound_prepared",
                actor_id=int(actor_id),
                before=lead_snapshot(lead),
                after=lead_snapshot(lead),
                details={
                    "outbox_id": int(outbox["id"]),
                    "platform": "telegram",
                },
                created_at=now_iso,
            )
    return {
        "outbox_id": int(outbox["id"]),
        "chat_id": int(chat_id),
        "message_text": text,
    }


def mark_sales_message_sent(
    *,
    outbox_id: int,
    provider_message_id: int | str,
) -> None:
    now_iso = iso_now()
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            outbox = rowdict(
                conn.execute(
                    """
                    SELECT *
                    FROM sales_outbound_messages
                    WHERE id=?
                    LIMIT 1
                    """.strip(),
                    (int(outbox_id),),
                ).fetchone()
            )
            if not outbox:
                raise ValueError("sales_outbound_not_found")
            if str(outbox.get("status") or "") == "sent":
                return
            if str(outbox.get("status") or "") != "prepared":
                raise ValueError("sales_outbound_not_prepared")

            lead = fetch_lead(conn, int(outbox["lead_id"]))
            if not lead:
                raise ValueError("sales_lead_not_found")
            before = lead_snapshot(lead)
            stage = str(lead.get("stage") or "new")
            next_stage = "contacted" if stage == "new" else stage
            stage_source = "manual" if stage == "new" else str(
                lead.get("stage_source") or "auto"
            )

            conn.execute(
                """
                UPDATE sales_outbound_messages
                SET status='sent', provider_message_id=?, error_code=NULL,
                    sent_at=?, updated_at=?
                WHERE id=? AND status='prepared'
                """.strip(),
                (str(provider_message_id), now_iso, now_iso, int(outbox_id)),
            )
            if changed_count(conn) != 1:
                raise RuntimeError("sales_outbound_concurrent_update")

            conn.execute(
                """
                UPDATE sales_leads
                SET stage=?, stage_source=?, last_contact_at=?,
                    updated_at=?, version=version+1
                WHERE id=? AND version=?
                """.strip(),
                (
                    next_stage,
                    stage_source,
                    now_iso,
                    now_iso,
                    int(lead["id"]),
                    int(lead.get("version") or 1),
                ),
            )
            if changed_count(conn) != 1:
                raise RuntimeError("sales_lead_concurrent_update")
            changed = fetch_lead(conn, int(lead["id"]))
            audit(
                conn,
                lead_id=int(lead["id"]),
                event_type="outbound_sent",
                actor_id=int(outbox["actor_id"]),
                before=before,
                after=lead_snapshot(changed),
                details={
                    "outbox_id": int(outbox_id),
                    "platform": "telegram",
                    "provider_message_id": str(provider_message_id),
                },
                created_at=now_iso,
            )


def mark_sales_message_failed(*, outbox_id: int, error_code: str) -> None:
    now_iso = iso_now()
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            outbox = rowdict(
                conn.execute(
                    """
                    SELECT *
                    FROM sales_outbound_messages
                    WHERE id=?
                    LIMIT 1
                    """.strip(),
                    (int(outbox_id),),
                ).fetchone()
            )
            if not outbox:
                raise ValueError("sales_outbound_not_found")
            if str(outbox.get("status") or "") != "prepared":
                return
            conn.execute(
                """
                UPDATE sales_outbound_messages
                SET status='failed', error_code=?, updated_at=?
                WHERE id=? AND status='prepared'
                """.strip(),
                (str(error_code or "send_failed")[:160], now_iso, int(outbox_id)),
            )
            if changed_count(conn) != 1:
                raise RuntimeError("sales_outbound_concurrent_update")
            lead = fetch_lead(conn, int(outbox["lead_id"]))
            audit(
                conn,
                lead_id=int(outbox["lead_id"]),
                event_type="outbound_failed",
                actor_id=int(outbox["actor_id"]),
                before=lead_snapshot(lead),
                after=lead_snapshot(lead),
                details={
                    "outbox_id": int(outbox_id),
                    "platform": "telegram",
                    "error_code": str(error_code or "send_failed")[:160],
                },
                created_at=now_iso,
            )
