from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from services.migrations.sales_desk_v5 import apply as apply_sales_desk_migration
from services.migrations.sales_desk_revenue_v6 import apply as apply_sales_revenue_migration


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            created_at TEXT,
            meta TEXT,
            payload TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            currency TEXT,
            provider_status TEXT,
            created_at TEXT
        )
        """
    )
    apply_sales_desk_migration(conn)
    apply_sales_revenue_migration(conn)
    conn.commit()
    return conn


def _patch_db(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    import services.sales_desk_repository as repository
    import services.sales_desk_sync as sync

    @contextmanager
    def fake_db():
        yield conn

    monkeypatch.setattr(repository, "db", fake_db)
    monkeypatch.setattr(sync, "db", fake_db)


def test_sync_discovers_lead_and_verified_payment_promotes_to_won(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.sales_desk as sales_desk

    conn = _connection()
    _patch_db(monkeypatch, conn)
    conn.execute(
        "INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)",
        (101, "anna", "Анна"),
    )
    conn.execute(
        """
        INSERT INTO events(user_id, name, created_at, meta, payload)
        VALUES(?,?,?,?,?)
        """.strip(),
        (
            101,
            "sub_menu_open",
            "2026-07-14T10:00:00+00:00",
            '{"source":"tgads","campaign":"summer"}',
            "{}",
        ),
    )
    conn.commit()

    result = sales_desk.sync_sales_leads()
    assert result["inserted"] == 1
    lead = sales_desk.sales_desk_snapshot(sync=False)["leads"][0]
    assert lead["stage"] == "qualified"
    assert lead["source"] == "tgads"
    assert lead["display_name"] == "Анна (@anna)"

    conn.execute(
        """
        INSERT INTO payments(
            user_id, amount, currency, provider_status, created_at
        ) VALUES(?,?,?,?,?)
        """.strip(),
        (101, 12900, "RUB", "succeeded", "2026-07-14T11:00:00+00:00"),
    )
    conn.commit()

    sales_desk.sync_sales_leads()
    won = sales_desk.get_lead(int(lead["id"]))
    assert won["stage"] == "won"
    assert won["revenue_minor"] == 12900
    assert any(
        item["event_type"] == "stage_auto_advanced"
        for item in won["audit"]
    )


def test_assignment_stage_follow_up_and_notes_are_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.sales_desk as sales_desk

    conn = _connection()
    _patch_db(monkeypatch, conn)
    conn.execute(
        "INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)",
        (202, "ivan", "Иван"),
    )
    conn.execute(
        """
        INSERT INTO events(user_id, name, created_at, meta, payload)
        VALUES(?,?,?,?,?)
        """.strip(),
        (202, "funnel_start_command", "2026-07-14T10:00:00+00:00", "{}", "{}"),
    )
    conn.commit()
    sales_desk.sync_sales_leads()
    lead_id = int(sales_desk.sales_desk_snapshot(sync=False)["leads"][0]["id"])

    claimed = sales_desk.claim_lead(lead_id=lead_id, actor_id=9001)
    assert claimed["assigned_to"] == 9001

    contacted = sales_desk.set_lead_stage(
        lead_id=lead_id,
        target_stage="contacted",
        actor_id=9001,
    )
    assert contacted["stage"] == "contacted"
    assert contacted["stage_source"] == "manual"

    scheduled = sales_desk.set_next_contact(
        lead_id=lead_id,
        days=3,
        actor_id=9001,
    )
    assert scheduled["next_contact_at"] is not None

    noted = sales_desk.add_note(
        lead_id=lead_id,
        actor_id=9001,
        note_text="Перезвонить после работы",
    )
    assert noted["notes"][0]["note_text"] == "Перезвонить после работы"
    event_types = {item["event_type"] for item in noted["audit"]}
    assert {
        "lead_assigned",
        "stage_changed",
        "follow_up_changed",
        "note_added",
    }.issubset(event_types)

    with pytest.raises(
        PermissionError,
        match="sales_lead_owned_by_another_admin",
    ):
        sales_desk.add_note(
            lead_id=lead_id,
            actor_id=9002,
            note_text="Нельзя тихо перехватить лид",
        )


def test_sync_does_not_rewrite_unchanged_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.sales_desk as sales_desk

    conn = _connection()
    _patch_db(monkeypatch, conn)
    conn.execute(
        "INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)",
        (303, "maria", "Мария"),
    )
    conn.execute(
        """
        INSERT INTO events(user_id, name, created_at, meta, payload)
        VALUES(?,?,?,?,?)
        """.strip(),
        (303, "demo_ack", "2026-07-14T10:00:00+00:00", "{}", "{}"),
    )
    conn.commit()

    first = sales_desk.sync_sales_leads()
    second = sales_desk.sync_sales_leads()
    assert first["inserted"] == 1
    assert second == {"inserted": 0, "updated": 0, "promoted": 0}


def test_migration_is_idempotent_and_locks_stage_values() -> None:
    conn = sqlite3.connect(":memory:")
    apply_sales_desk_migration(conn)
    apply_sales_desk_migration(conn)
    conn.execute(
        """
        INSERT INTO sales_leads(
            lead_key, user_id, display_name, stage, stage_source,
            revenue_minor, currency, version, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        ("user:1", 1, "Lead", "new", "auto", 0, "RUB", 1, "now", "now"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO sales_leads(
                lead_key, user_id, display_name, stage, stage_source,
                revenue_minor, currency, version, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "user:2",
                2,
                "Bad",
                "invented",
                "auto",
                0,
                "RUB",
                1,
                "now",
                "now",
            ),
        )


def test_mixed_rub_and_stars_revenue_is_kept_in_separate_currencies(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.sales_desk as sales_desk

    conn = _connection()
    _patch_db(monkeypatch, conn)
    conn.execute("INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)", (404, "mix", "Микс"))
    conn.execute("INSERT INTO events(user_id, name, created_at, meta, payload) VALUES(?,?,?,?,?)", (404, "payment_success", "2026-07-14T10:00:00+00:00", "{}", "{}"))
    conn.execute("INSERT INTO payments(user_id, amount, currency, provider_status, created_at) VALUES(?,?,?,?,?)", (404, 190000, "RUB", "succeeded", "2026-07-14T11:00:00+00:00"))
    conn.execute("INSERT INTO payments(user_id, amount, currency, provider_status, created_at) VALUES(?,?,?,?,?)", (404, 1226, "XTR", "succeeded", "2026-07-14T12:00:00+00:00"))
    conn.commit()

    sales_desk.sync_sales_leads()
    lead = sales_desk.sales_desk_snapshot(filter_name="won", sync=False)["leads"][0]
    assert lead["revenue_by_currency"] == {"RUB": 190000, "XTR": 1226}
    card = sales_desk.format_lead_card(lead)
    assert "1 900.00 ₽" in card
    assert "1226 ⭐" in card


def test_refund_reduces_sales_revenue_instead_of_preserving_historical_max(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.sales_desk as sales_desk

    conn = _connection()
    _patch_db(monkeypatch, conn)
    conn.execute("INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)", (505, "refund", "Возврат"))
    conn.execute("INSERT INTO events(user_id, name, created_at, meta, payload) VALUES(?,?,?,?,?)", (505, "payment_success", "2026-07-14T10:00:00+00:00", "{}", "{}"))
    conn.execute("INSERT INTO payments(user_id, amount, currency, provider_status, created_at) VALUES(?,?,?,?,?)", (505, 790000, "RUB", "succeeded", "2026-07-14T11:00:00+00:00"))
    conn.commit()
    sales_desk.sync_sales_leads()
    assert sales_desk.get_lead(1)["revenue_by_currency"] == {"RUB": 790000}

    conn.execute("UPDATE payments SET provider_status='refunded' WHERE user_id=505")
    conn.commit()
    sales_desk.sync_sales_leads()
    lead = sales_desk.get_lead(1)
    assert lead["revenue_by_currency"] == {}
    assert int(lead["revenue_minor"]) == 0


def test_revenue_reset_does_not_overwrite_manual_stage_or_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.sales_desk as sales_desk

    conn = _connection()
    _patch_db(monkeypatch, conn)
    conn.execute(
        "INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)",
        (606, "preserve", "Сохранить"),
    )
    conn.execute(
        """
        INSERT INTO events(user_id, name, created_at, meta, payload)
        VALUES(?,?,?,?,?)
        """.strip(),
        (
            606,
            "payment_success",
            "2026-07-14T10:00:00+00:00",
            '{"source":"vkads","campaign":"protected"}',
            "{}",
        ),
    )
    conn.execute(
        """
        INSERT INTO payments(user_id, amount, currency, provider_status, created_at)
        VALUES(?,?,?,?,?)
        """.strip(),
        (606, 190000, "RUB", "succeeded", "2026-07-14T11:00:00+00:00"),
    )
    conn.commit()
    sales_desk.sync_sales_leads()

    conn.execute(
        """
        UPDATE sales_leads
        SET stage='contacted', stage_source='manual', assigned_to=9001
        WHERE user_id=606
        """.strip()
    )
    conn.execute("DELETE FROM events WHERE user_id=606")
    conn.execute("UPDATE payments SET provider_status='refunded' WHERE user_id=606")
    conn.commit()

    sales_desk.sync_sales_leads()
    lead = sales_desk.get_lead(1)
    assert lead["revenue_by_currency"] == {}
    assert int(lead["revenue_minor"]) == 0
    assert lead["source"] == "vkads"
    assert lead["campaign"] == "protected"
    assert lead["stage"] == "contacted"
    assert lead["stage_source"] == "manual"
    assert int(lead["assigned_to"]) == 9001
