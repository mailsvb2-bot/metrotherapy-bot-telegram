from __future__ import annotations

import importlib


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")
    monkeypatch.setenv("METRO_DB_PATH", str(tmp_path / "webhook-dead-letter.db"))
    monkeypatch.setenv("DATABASE_URL", "")

    module_names = [
        "core.paths",
        "services.db.runtime",
        "services.db.core",
        "services.migrations",
        "services.schema_core",
        "services.schema",
        "services.messenger.webhook_dedupe",
    ]
    modules = {}
    for name in module_names:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)
    modules["services.schema"].init_db()
    return modules["services.messenger.webhook_dedupe"], modules["services.db.core"]


def test_poison_event_transitions_to_dead_letter_and_stops_retrying(monkeypatch, tmp_path):
    dedupe, db_core = _reload(monkeypatch, tmp_path)
    payload = {"type": "message_new", "object": {"message": {"id": 55}}}

    first = dedupe.record_inbound_failure(
        "vk",
        "poison-55",
        payload,
        "extraction_failed",
        max_attempts=2,
    )
    second = dedupe.record_inbound_failure(
        "vk",
        "poison-55",
        payload,
        "extraction_failed",
        max_attempts=2,
    )
    duplicate = dedupe.record_inbound_failure(
        "vk",
        "poison-55",
        payload,
        "extraction_failed",
        max_attempts=2,
    )

    assert first.attempts == 1
    assert first.retryable is True
    assert first.dead_lettered is False
    assert second.attempts == 2
    assert second.retryable is False
    assert second.dead_lettered is True
    assert duplicate.attempts == 2
    assert duplicate.recorded is False
    assert duplicate.dead_lettered is True
    assert dedupe.claim_inbound_event("vk", "poison-55", payload) is False

    with db_core.db() as conn:
        row = conn.execute(
            "SELECT status,attempts,completed_at,last_error FROM messenger_webhook_events "
            "WHERE platform=? AND event_key=?",
            ("vk", "poison-55"),
        ).fetchone()
    assert row["status"] == "dead_letter"
    assert int(row["attempts"]) == 2
    assert row["completed_at"]
    assert "extraction_failed" in row["last_error"]


def test_completed_event_is_never_downgraded_to_failure(monkeypatch, tmp_path):
    dedupe, db_core = _reload(monkeypatch, tmp_path)
    payload = {"type": "message_created", "message": {"id": "done-1"}}

    assert dedupe.claim_inbound_event("max", "done-1", payload) is True
    dedupe.complete_inbound_event("max", "done-1", payload)
    result = dedupe.record_inbound_failure(
        "max",
        "done-1",
        payload,
        "late extraction failure",
        max_attempts=1,
    )

    assert result.recorded is False
    assert result.dead_lettered is False
    with db_core.db() as conn:
        row = conn.execute(
            "SELECT status,attempts,last_error FROM messenger_webhook_events "
            "WHERE platform=? AND event_key=?",
            ("max", "done-1"),
        ).fetchone()
    assert row["status"] == "completed"
    assert int(row["attempts"]) == 1
    assert row["last_error"] == ""
