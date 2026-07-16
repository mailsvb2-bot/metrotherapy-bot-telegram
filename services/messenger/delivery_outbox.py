from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from core.time_utils import utc_now, utc_now_iso
from runtime.messenger_senders import MessengerTransportError
from services.bg import tm
from services.db import db, tx
from services.db.runtime import CONFIG
from services.events import log_event
from services.messenger.observability import log_action_completed
from services.messenger.reply_dispatcher import send_reply_bundle
from services.messenger.text_ui import MessengerReply

log = logging.getLogger(__name__)
_ALLOWED_PLATFORMS = {"vk", "max"}
_worker_task: asyncio.Task | None = None
_worker_stop: asyncio.Event | None = None


@dataclass(frozen=True)
class ClaimedDelivery:
    id: int
    platform: str
    external_user_id: str
    canonical_user_id: int
    event_key: str
    action: str
    replies_json: str
    attempts: int
    lock_token: str


def _row_value(row: Any, key: str, index: int) -> Any:
    return row[key] if hasattr(row, "keys") else row[index]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def serialize_replies(replies: list[MessengerReply]) -> str:
    return json.dumps(
        [
            {
                "kind": str(reply.kind or "text"),
                "text": str(reply.text or ""),
                "meta": _json_safe(dict(reply.meta or {})),
            }
            for reply in replies
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def deserialize_replies(raw: str) -> list[MessengerReply]:
    loaded = json.loads(str(raw or "[]"))
    if not isinstance(loaded, list):
        raise ValueError("messenger outbox replies_json must be a list")
    replies: list[MessengerReply] = []
    for item in loaded:
        if not isinstance(item, dict):
            raise ValueError("messenger outbox reply must be an object")
        meta = item.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        replies.append(
            MessengerReply(
                kind=str(item.get("kind") or "text"),
                text=str(item.get("text") or ""),
                meta={str(key): _json_safe(value) for key, value in meta.items()},
            )
        )
    return replies


def persist_reply_bundle(
    *,
    platform: str,
    external_user_id: str,
    canonical_user_id: int,
    event_key: str,
    replies: list[MessengerReply],
    action: str,
) -> bool:
    normalized_platform = str(platform or "").strip().lower()
    if normalized_platform not in _ALLOWED_PLATFORMS:
        raise ValueError(f"unsupported messenger outbox platform: {normalized_platform!r}")
    normalized_event_key = str(event_key or "").strip()
    normalized_external_id = str(external_user_id or "").strip()
    if not normalized_event_key:
        raise ValueError("messenger outbox event_key is required")
    if not normalized_external_id:
        raise ValueError("messenger outbox external_user_id is required")

    now = utc_now_iso()
    encoded = serialize_replies(list(replies))
    values = (
        normalized_platform,
        normalized_external_id,
        int(canonical_user_id),
        normalized_event_key,
        str(action or "")[:240],
        encoded,
        now,
        now,
        now,
    )
    with db() as conn:
        with tx(conn):
            insert_sql = (
                "INSERT INTO messenger_delivery_outbox("
                "platform,external_user_id,canonical_user_id,event_key,action,replies_json,"
                "status,attempts,available_at,locked_at,lock_token,last_error,created_at,updated_at,sent_at"
                ") VALUES(?,?,?,?,?,?, 'pending',0,?,NULL,NULL,'',?,?,NULL) "
                "ON CONFLICT (platform,event_key) DO NOTHING"
                if CONFIG.uses_postgres
                else
                "INSERT OR IGNORE INTO messenger_delivery_outbox("
                "platform,external_user_id,canonical_user_id,event_key,action,replies_json,"
                "status,attempts,available_at,locked_at,lock_token,last_error,created_at,updated_at,sent_at"
                ") VALUES(?,?,?,?,?,?, 'pending',0,?,NULL,NULL,'',?,?,NULL)"
            )
            cursor = conn.execute(insert_sql, values)
            inserted = int(getattr(cursor, "rowcount", 0) or 0) == 1
            conn.execute(
                "UPDATE messenger_webhook_events "
                "SET status='completed',completed_at=?,updated_at=?,last_error='' "
                "WHERE platform=? AND event_key=?",
                (now, now, normalized_platform, normalized_event_key),
            )
    return inserted


def _claimed_from_rows(rows: list[Any], token: str) -> list[ClaimedDelivery]:
    return [
        ClaimedDelivery(
            id=int(_row_value(row, "id", 0)),
            platform=str(_row_value(row, "platform", 1)),
            external_user_id=str(_row_value(row, "external_user_id", 2)),
            canonical_user_id=int(_row_value(row, "canonical_user_id", 3)),
            event_key=str(_row_value(row, "event_key", 4)),
            action=str(_row_value(row, "action", 5) or ""),
            replies_json=str(_row_value(row, "replies_json", 6) or "[]"),
            attempts=int(_row_value(row, "attempts", 7) or 0),
            lock_token=str(_row_value(row, "lock_token", 8) or token),
        )
        for row in rows
    ]


def claim_due_deliveries(*, limit: int = 20, lock_ttl_sec: int = 120) -> list[ClaimedDelivery]:
    """Atomically lease due work, including abandoned ``sending`` rows."""

    now = utc_now().replace(microsecond=0)
    now_iso = now.isoformat()
    stale_before = (now - timedelta(seconds=max(1, int(lock_ttl_sec)))).isoformat()
    token = uuid.uuid4().hex
    due_predicate = (
        "((status IN ('pending','retry') AND available_at<=?) "
        "OR (status='sending' AND locked_at IS NOT NULL AND locked_at<=?))"
    )

    with db() as conn:
        with tx(conn):
            if CONFIG.uses_postgres:
                rows = conn.execute(
                    f"""
                    WITH due AS (
                        SELECT id FROM messenger_delivery_outbox
                        WHERE {due_predicate}
                        ORDER BY id ASC LIMIT ? FOR UPDATE SKIP LOCKED
                    )
                    UPDATE messenger_delivery_outbox
                    SET status='sending',locked_at=?,lock_token=?,updated_at=?
                    FROM due
                    WHERE messenger_delivery_outbox.id=due.id
                    RETURNING messenger_delivery_outbox.id,messenger_delivery_outbox.platform,
                              messenger_delivery_outbox.external_user_id,
                              messenger_delivery_outbox.canonical_user_id,
                              messenger_delivery_outbox.event_key,messenger_delivery_outbox.action,
                              messenger_delivery_outbox.replies_json,messenger_delivery_outbox.attempts,
                              messenger_delivery_outbox.lock_token
                    """.strip(),
                    (now_iso, stale_before, int(limit), now_iso, token, now_iso),
                ).fetchall()
                return _claimed_from_rows(list(rows), token)

            rows = conn.execute(
                f"SELECT id FROM messenger_delivery_outbox WHERE {due_predicate} ORDER BY id ASC LIMIT ?",  # nosec B608
                (now_iso, stale_before, int(limit)),
            ).fetchall()
            ids = [int(_row_value(row, "id", 0)) for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                "UPDATE messenger_delivery_outbox "
                "SET status='sending',locked_at=?,lock_token=?,updated_at=? "
                f"WHERE id IN ({placeholders}) AND {due_predicate}",  # nosec B608
                [now_iso, token, now_iso, *ids, now_iso, stale_before],
            )
            claimed = conn.execute(
                "SELECT id,platform,external_user_id,canonical_user_id,event_key,action,replies_json,attempts,lock_token "
                "FROM messenger_delivery_outbox WHERE lock_token=? "
                f"AND id IN ({placeholders}) ORDER BY id",  # nosec B608
                [token, *ids],
            ).fetchall()
            return _claimed_from_rows(list(claimed), token)


def mark_delivery_sent(item: ClaimedDelivery) -> None:
    now = utc_now_iso()
    with db() as conn:
        with tx(conn):
            conn.execute(
                "UPDATE messenger_delivery_outbox "
                "SET status='sent',sent_at=?,updated_at=?,locked_at=NULL,lock_token=NULL,last_error='' "
                "WHERE id=? AND lock_token=?",
                (now, now, int(item.id), item.lock_token),
            )


def reschedule_delivery(item: ClaimedDelivery, error: str) -> None:
    attempts = int(item.attempts) + 1
    try:
        configured_max = int(os.getenv("MESSENGER_OUTBOX_MAX_ATTEMPTS", "8") or "8")
    except ValueError:
        configured_max = 8
    max_attempts = max(1, configured_max)
    now = utc_now().replace(microsecond=0)
    terminal = attempts >= max_attempts
    delay = min(5 * (2 ** max(0, attempts - 1)), 900)
    available_at = (now + timedelta(seconds=delay)).isoformat()
    status = "dead" if terminal else "retry"
    with db() as conn:
        with tx(conn):
            conn.execute(
                "UPDATE messenger_delivery_outbox "
                "SET status=?,attempts=?,available_at=?,updated_at=?,locked_at=NULL,lock_token=NULL,last_error=? "
                "WHERE id=? AND lock_token=?",
                (status, attempts, available_at, now.isoformat(), str(error or "")[:500], int(item.id), item.lock_token),
            )
    if terminal:
        log_event(
            item.canonical_user_id,
            f"{item.platform}_delivery_dead_letter",
            {"event_key": item.event_key, "attempts": attempts, "error": str(error or "")[:180]},
        )


async def _deliver_one(item: ClaimedDelivery) -> None:
    replies = deserialize_replies(item.replies_json)
    await send_reply_bundle(item.platform, item.external_user_id, item.canonical_user_id, replies)
    await asyncio.to_thread(mark_delivery_sent, item)
    await asyncio.to_thread(
        log_action_completed,
        platform=item.platform,
        user_id=item.canonical_user_id,
        action=item.action,
        replies=len(replies),
        status="ok",
    )


async def _worker_loop(stop_event: asyncio.Event) -> None:
    try:
        idle_sleep = max(0.1, float(os.getenv("MESSENGER_OUTBOX_IDLE_SLEEP_SEC", "0.5") or "0.5"))
    except ValueError:
        idle_sleep = 0.5
    while not stop_event.is_set():
        try:
            claimed = await asyncio.to_thread(claim_due_deliveries)
            if not claimed:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=idle_sleep)
                except asyncio.TimeoutError:
                    pass
                continue
            for item in claimed:
                if stop_event.is_set():
                    return
                try:
                    await _deliver_one(item)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # validator: allow-wide-except
                    log.exception(
                        "%s durable delivery failed event_key=%s attempt=%s",
                        item.platform.upper(),
                        item.event_key,
                        int(item.attempts) + 1,
                    )
                    await asyncio.to_thread(reschedule_delivery, item, f"{type(exc).__name__}: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception:  # validator: allow-wide-except
            log.exception("Messenger durable delivery worker tick failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=idle_sleep)
            except asyncio.TimeoutError:
                pass


def start_delivery_worker() -> asyncio.Task:
    global _worker_task, _worker_stop
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    _worker_stop = asyncio.Event()
    _worker_task = tm().create(_worker_loop(_worker_stop), name="messenger_durable_delivery_worker")
    return _worker_task


async def stop_delivery_worker() -> None:
    global _worker_task, _worker_stop
    task = _worker_task
    if task is None:
        return
    if _worker_stop is not None:
        _worker_stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _worker_task = None
    _worker_stop = None


def outbox_snapshot() -> dict[str, int]:
    with db() as conn:
        rows = conn.execute(
            "SELECT status,COUNT(*) AS count FROM messenger_delivery_outbox GROUP BY status"
        ).fetchall()
    counts = {str(_row_value(row, "status", 0)): int(_row_value(row, "count", 1) or 0) for row in rows}
    return {
        "pending": counts.get("pending", 0),
        "retry": counts.get("retry", 0),
        "sending": counts.get("sending", 0),
        "sent": counts.get("sent", 0),
        "dead": counts.get("dead", 0),
    }
