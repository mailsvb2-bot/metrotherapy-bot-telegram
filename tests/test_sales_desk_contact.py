from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from services.migrations.sales_desk_v5 import apply as apply_sales_desk_migration
from services.migrations.sales_desk_revenue_v6 import apply as apply_sales_revenue_migration


def _connection(*, with_identities: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_sales_desk_migration(conn)
    apply_sales_revenue_migration(conn)
    if with_identities:
        conn.execute(
            """
            CREATE TABLE account_channel_identities (
                account_id BIGINT NOT NULL,
                platform TEXT NOT NULL,
                external_user_id TEXT NOT NULL
            )
            """
        )
    conn.execute(
        """
        INSERT INTO sales_leads(
            lead_key, user_id, display_name, stage, stage_source,
            revenue_minor, currency, version, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "user:101",
            101,
            "Анна",
            "new",
            "auto",
            0,
            "RUB",
            1,
            "2026-07-14T10:00:00+00:00",
            "2026-07-14T10:00:00+00:00",
        ),
    )
    conn.commit()
    return conn


def _patch_db(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    import services.sales_desk_contact as contact
    import services.sales_desk_repository as repository

    @contextmanager
    def fake_db():
        yield conn

    monkeypatch.setattr(contact, "db", fake_db)
    monkeypatch.setattr(repository, "db", fake_db)


def _outbox(conn: sqlite3.Connection, outbox_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM sales_outbound_messages WHERE id=?",
        (int(outbox_id),),
    ).fetchone()
    assert row is not None
    return row


def test_prepared_message_is_sent_once_and_advances_new_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.sales_desk_contact import (
        mark_sales_message_sent,
        prepare_sales_message,
    )

    conn = _connection()
    _patch_db(monkeypatch, conn)

    prepared = prepare_sales_message(
        lead_id=1,
        actor_id=9001,
        message_text="Анна, добрый день! Чем помочь?",
    )
    assert prepared["chat_id"] == 101
    assert _outbox(conn, prepared["outbox_id"])["status"] == "prepared"

    mark_sales_message_sent(
        outbox_id=prepared["outbox_id"],
        provider_message_id=777,
    )
    sent = _outbox(conn, prepared["outbox_id"])
    assert sent["status"] == "sent"
    assert sent["provider_message_id"] == "777"

    lead = conn.execute("SELECT * FROM sales_leads WHERE id=1").fetchone()
    assert lead is not None
    assert lead["assigned_to"] == 9001
    assert lead["stage"] == "contacted"
    assert lead["stage_source"] == "manual"
    assert lead["last_contact_at"] is not None

    event_types = {
        row["event_type"]
        for row in conn.execute(
            "SELECT event_type FROM sales_lead_audit WHERE lead_id=1"
        ).fetchall()
    }
    assert {"lead_assigned", "outbound_prepared", "outbound_sent"}.issubset(
        event_types
    )

    mark_sales_message_sent(
        outbox_id=prepared["outbox_id"],
        provider_message_id=777,
    )
    assert _outbox(conn, prepared["outbox_id"])["status"] == "sent"


def test_known_telegram_rejection_is_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.sales_desk_contact import (
        mark_sales_message_failed,
        prepare_sales_message,
    )

    conn = _connection()
    _patch_db(monkeypatch, conn)
    prepared = prepare_sales_message(
        lead_id=1,
        actor_id=9001,
        message_text="Тест",
    )

    mark_sales_message_failed(
        outbox_id=prepared["outbox_id"],
        error_code="TelegramForbiddenError",
    )
    row = _outbox(conn, prepared["outbox_id"])
    assert row["status"] == "failed"
    assert row["error_code"] == "TelegramForbiddenError"


def test_timeout_is_uncertain_and_cannot_be_retried_as_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.sales_desk_contact import (
        mark_sales_message_failed,
        mark_sales_message_sent,
        prepare_sales_message,
    )

    conn = _connection()
    _patch_db(monkeypatch, conn)
    prepared = prepare_sales_message(
        lead_id=1,
        actor_id=9001,
        message_text="Тест таймаута",
    )

    mark_sales_message_failed(
        outbox_id=prepared["outbox_id"],
        error_code="TimeoutError",
    )
    row = _outbox(conn, prepared["outbox_id"])
    assert row["status"] == "uncertain"

    with pytest.raises(ValueError, match="sales_outbound_not_prepared"):
        mark_sales_message_sent(
            outbox_id=prepared["outbox_id"],
            provider_message_id=888,
        )


def test_linked_non_telegram_identity_blocks_numeric_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.sales_desk_contact import prepare_sales_message

    conn = _connection(with_identities=True)
    conn.execute(
        """
        INSERT INTO account_channel_identities(
            account_id, platform, external_user_id
        ) VALUES(?,?,?)
        """,
        (101, "max", "max-user-101"),
    )
    conn.commit()
    _patch_db(monkeypatch, conn)

    with pytest.raises(ValueError, match="sales_telegram_identity_missing"):
        prepare_sales_message(
            lead_id=1,
            actor_id=9001,
            message_text="Не должно уйти в неправильный канал",
        )

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM sales_outbound_messages"
    ).fetchone()["n"]
    assert count == 0


def test_another_manager_cannot_prepare_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.sales_desk_contact import prepare_sales_message

    conn = _connection()
    conn.execute("UPDATE sales_leads SET assigned_to=9001 WHERE id=1")
    conn.commit()
    _patch_db(monkeypatch, conn)

    with pytest.raises(
        PermissionError,
        match="sales_lead_owned_by_another_admin",
    ):
        prepare_sales_message(
            lead_id=1,
            actor_id=9002,
            message_text="Перехват запрещён",
        )
