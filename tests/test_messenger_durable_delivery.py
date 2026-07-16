import importlib
import json

import pytest


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")
    monkeypatch.setenv("METRO_DB_PATH", str(tmp_path / "durable-delivery.db"))
    monkeypatch.setenv("DATABASE_URL", "")

    module_names = [
        "core.paths",
        "services.db.runtime",
        "services.db.core",
        "services.migrations",
        "services.schema_core",
        "services.schema",
        "services.messenger.webhook_dedupe",
        "services.messenger.delivery_outbox",
    ]
    modules = {}
    for name in module_names:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)
    modules["services.schema"].init_db()
    return (
        modules["services.messenger.webhook_dedupe"],
        modules["services.messenger.delivery_outbox"],
        modules["services.db.core"],
    )


def test_inbound_claim_can_retry_failed_event(monkeypatch, tmp_path):
    dedupe, _, _ = _reload(monkeypatch, tmp_path)
    payload = {"type": "message_new", "object": {"message": {"id": 1}}}

    assert dedupe.claim_inbound_event("vk", "event-1", payload) is True
    assert dedupe.claim_inbound_event("vk", "event-1", payload) is False

    dedupe.fail_inbound_event("vk", "event-1", payload, "synthetic failure")
    assert dedupe.claim_inbound_event("vk", "event-1", payload) is True


def test_persist_reply_bundle_completes_inbound_and_is_idempotent(monkeypatch, tmp_path):
    dedupe, outbox, db_core = _reload(monkeypatch, tmp_path)
    from services.messenger.text_ui import MessengerReply

    payload = {"type": "message_new", "object": {"message": {"id": 2}}}
    assert dedupe.claim_inbound_event("vk", "event-2", payload) is True

    inserted = outbox.persist_reply_bundle(
        platform="vk",
        external_user_id="vk-42",
        canonical_user_id=42,
        event_key="event-2",
        replies=[MessengerReply(text="hello", meta={"session_id": "7"})],
        action="menu",
    )
    assert inserted is True

    duplicate = outbox.persist_reply_bundle(
        platform="vk",
        external_user_id="vk-42",
        canonical_user_id=42,
        event_key="event-2",
        replies=[MessengerReply(text="hello")],
        action="menu",
    )
    assert duplicate is False

    with db_core.db() as conn:
        event = conn.execute(
            "SELECT status, completed_at FROM messenger_webhook_events WHERE platform=? AND event_key=?",
            ("vk", "event-2"),
        ).fetchone()
        rows = conn.execute(
            "SELECT replies_json, status FROM messenger_delivery_outbox WHERE platform=? AND event_key=?",
            ("vk", "event-2"),
        ).fetchall()

    assert event["status"] == "completed"
    assert event["completed_at"]
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert json.loads(rows[0]["replies_json"])[0]["text"] == "hello"


def test_claim_and_mark_delivery_sent(monkeypatch, tmp_path):
    dedupe, outbox, _ = _reload(monkeypatch, tmp_path)
    from services.messenger.text_ui import MessengerReply

    payload = {"type": "message_created", "message": {"id": "max-1"}}
    assert dedupe.claim_inbound_event("max", "max-event-1", payload) is True
    outbox.persist_reply_bundle(
        platform="max",
        external_user_id="max-42",
        canonical_user_id=42,
        event_key="max-event-1",
        replies=[MessengerReply(kind="text", text="ready")],
        action="continue",
    )

    claimed = outbox.claim_due_deliveries(limit=5)
    assert len(claimed) == 1
    assert claimed[0].platform == "max"
    assert outbox.deserialize_replies(claimed[0].replies_json)[0].text == "ready"

    outbox.mark_delivery_sent(claimed[0])
    assert outbox.outbox_snapshot()["sent"] == 1


def test_delivery_failure_is_rescheduled(monkeypatch, tmp_path):
    dedupe, outbox, db_core = _reload(monkeypatch, tmp_path)
    from services.messenger.text_ui import MessengerReply

    payload = {"type": "message_created", "message": {"id": "max-2"}}
    assert dedupe.claim_inbound_event("max", "max-event-2", payload) is True
    outbox.persist_reply_bundle(
        platform="max",
        external_user_id="max-43",
        canonical_user_id=43,
        event_key="max-event-2",
        replies=[MessengerReply(text="retry me")],
        action="continue",
    )
    item = outbox.claim_due_deliveries(limit=1)[0]
    outbox.reschedule_delivery(item, "transport down")

    with db_core.db() as conn:
        row = conn.execute(
            "SELECT status, attempts, last_error FROM messenger_delivery_outbox WHERE id=?",
            (item.id,),
        ).fetchone()
    assert row["status"] == "retry"
    assert int(row["attempts"]) == 1
    assert "transport down" in row["last_error"]
