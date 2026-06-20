from __future__ import annotations

from services.db import db
from services.privacy_controls import erase_user_behavioral_data, export_user_data_snapshot


def test_privacy_erase_anonymizes_behavioral_data_and_retains_financial_facts():
    uid = 987654321

    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO users(user_id, joined_at, username, first_name, work_time, home_time, demo_uses)
            VALUES(?,?,?,?,?,?,?)
            """.strip(),
            (uid, "2026-01-01", "private_user", "Private", "08:30", "19:30", 2),
        )
        conn.execute(
            "INSERT INTO events(user_id, event, ts, meta) VALUES(?,?,?,?)",
            (uid, "mood_score", "2026-01-01T00:00:00+00:00", '{"score":3}'),
        )
        conn.execute(
            """
            INSERT INTO mood_sessions(user_id, kind, source, day, slot, pre_score, post_score, created_at_utc)
            VALUES(?,?,?,?,?,?,?,?)
            """.strip(),
            (uid, "work", "demo", "2026-01-01", "demo", 1, 4, "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO payments(user_id, telegram_charge_id, provider_charge_id, payload, amount, currency, created_at)
            VALUES(?,?,?,?,?,?,?)
            """.strip(),
            (uid, "tg_charge_privacy", "provider_privacy", "yookassa:tokens", 190000, "RUB", "2026-01-01"),
        )

    snapshot = export_user_data_snapshot(uid)
    assert snapshot["tables"]["users"][0]["username"] == "private_user"
    assert len(snapshot["tables"]["events"]) == 1
    assert len(snapshot["tables"]["payments"]) == 1

    result = erase_user_behavioral_data(uid, reason="test")
    assert result.user_id == uid
    assert result.anonymized_profile is True
    assert result.deleted_tables["events"] == 1
    assert result.deleted_tables["mood_sessions"] == 1
    assert "payments" in result.retained_tables

    with db() as conn:
        user = conn.execute("SELECT username, first_name, work_time, home_time, demo_uses FROM users WHERE user_id=?", (uid,)).fetchone()
        assert user["username"] is None
        assert user["first_name"] is None
        assert user["work_time"] is None
        assert user["home_time"] is None
        assert int(user["demo_uses"]) == 0

        assert conn.execute("SELECT COUNT(*) AS c FROM events WHERE user_id=?", (uid,)).fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM mood_sessions WHERE user_id=?", (uid,)).fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM payments WHERE user_id=?", (uid,)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM privacy_erasure_log WHERE user_id=?", (uid,)).fetchone()["c"] == 1
