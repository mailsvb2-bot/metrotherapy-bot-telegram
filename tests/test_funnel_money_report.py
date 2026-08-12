from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from handlers.admin_reports import conversion as conversion_handler
from services import funnel_analytics, funnel_analytics_indexes
from services.db import db


def test_commercial_funnel_index_is_selective_and_concurrent() -> None:
    assert len(funnel_analytics_indexes.ONLINE_INDEX_SPECS) == 1
    spec = funnel_analytics_indexes.ONLINE_INDEX_SPECS[0]
    normalized = " ".join(spec.ddl.split())

    assert spec.name == "idx_events_commercial_funnel_v1"
    assert "CREATE INDEX CONCURRENTLY" in normalized
    assert "ON events (name, created_at, user_id)" in normalized
    assert "WHERE name IN" in normalized
    for event_name in ("demo_sent", "view_tariffs", "payment_started", "sub_paid"):
        assert event_name in normalized


def test_funnel_index_manager_reuses_shared_online_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_runner(specs, **kwargs):
        captured["specs"] = specs
        captured.update(kwargs)
        return {"engine": "postgres", "status": "ready", "indexes": []}

    monkeypatch.setattr(funnel_analytics_indexes, "ensure_online_indexes", fake_runner)

    result = funnel_analytics_indexes.ensure_funnel_analytics_indexes()

    assert result["status"] == "ready"
    assert captured["specs"] == funnel_analytics_indexes.ONLINE_INDEX_SPECS
    assert captured["component"] == "FunnelAnalytics"
    assert captured["statement_timeout_env"] == "FUNNEL_INDEX_STATEMENT_TIMEOUT_SEC"
    assert captured["lock_timeout_env"] == "FUNNEL_INDEX_LOCK_TIMEOUT_SEC"


def _seed_money_funnel() -> None:
    rows = [
        (-941001, "demo_sent", "2099-08-01T10:00:00+00:00"),
        (-941002, "demo_sent", "2099-08-01T10:01:00+00:00"),
        (-941003, "demo_sent", "2099-08-01T10:02:00+00:00"),
        (-941001, "demo_ack", "2099-08-01T10:03:00+00:00"),
        (-941002, "demo_ack", "2099-08-01T10:04:00+00:00"),
        (-941001, "view_tariffs", "2099-08-01T10:05:00+00:00"),
        (-941002, "view_tariffs", "2099-08-01T10:06:00+00:00"),
        (-941001, "payment_started", "2099-08-01T10:07:00+00:00"),
    ]
    with db() as conn:
        conn.executemany(
            "INSERT INTO events(user_id, name, meta, created_at) VALUES(?,?,?,?)",
            [(user_id, name, "{}", created_at) for user_id, name, created_at in rows],
        )
        conn.executemany(
            """
            INSERT INTO payment_token_grants(
                provider, provider_payment_id, user_id, package_id,
                tokens_granted, ledger_id, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """.strip(),
            [
                ("yookassa", "funnel-paid-1", -941001, "practice_60", 60, None, "2099-08-01T10:08:00+00:00"),
                ("yookassa", "funnel-paid-1-repeat", -941001, "practice_60", 60, None, "2099-08-02T10:08:00+00:00"),
            ],
        )
        conn.commit()


def test_conversion_report_uses_commercial_steps_and_authoritative_paid_users() -> None:
    _seed_money_funnel()

    report = funnel_analytics.conversion_report(
        "2099-08-01T00:00:00+00:00",
        "2099-09-01T00:00:00+00:00",
    )

    assert report["counts"]["demo_sent"] == 3
    assert report["counts"]["demo_ack"] == 2
    assert report["counts"]["view_tariffs"] == 2
    assert report["counts"]["payment_started"] == 1
    assert report["paid_users"] == 1

    chain = report["money_chain"]
    assert [item["users"] for item in chain] == [3, 2, 2, 1, 1]
    assert chain[1]["from_prev_pct"] == pytest.approx(66.7)
    assert chain[2]["from_prev_pct"] == pytest.approx(100.0)
    assert chain[3]["from_prev_pct"] == pytest.approx(50.0)
    assert chain[4]["from_prev_pct"] == pytest.approx(100.0)

    text = funnel_analytics.format_conversion_report(report, title="контрольный период")
    assert "💰 Воронка до оплаты" in text
    assert "контрольный период" in text
    assert "Создан checkout: 1" in text
    assert "Оплатили пакет: 1" in text
    assert "Самый слабый переход: Создан checkout — 50.0%" in text
    assert "payment_token_grants" in text


def test_commercial_counts_use_one_grouped_events_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, tuple[Any, ...]]] = []

    class FakeConnection:
        def execute(self, sql: str, params: tuple[Any, ...]):
            executed.append((sql, params))
            return self

        def fetchall(self):
            return [
                {"name": "demo_sent", "cnt": 7},
                {"name": "view_tariffs", "cnt": 3},
            ]

    @contextmanager
    def fake_db():
        yield FakeConnection()

    monkeypatch.setattr(funnel_analytics, "db", fake_db)

    counts = funnel_analytics._counts(
        ["demo_sent", "view_tariffs"],
        "2099-08-01T00:00:00+00:00",
        "2099-09-01T00:00:00+00:00",
    )

    assert counts == {"demo_sent": 7, "view_tariffs": 3}
    assert len(executed) == 1
    sql, params = executed[0]
    assert "COUNT(DISTINCT user_id)" in sql
    assert "GROUP BY name" in sql
    assert funnel_analytics_indexes.COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL in sql
    assert params[:2] == ("demo_sent", "view_tariffs")


def test_rate_chain_and_empty_format_are_stable() -> None:
    chain = funnel_analytics._rate_chain([("a", 0), ("b", 0)])
    assert chain[0]["from_prev_pct"] is None
    assert chain[1]["from_prev_pct"] == 0.0
    assert chain[1]["dropped_from_prev"] == 0

    text = funnel_analytics.format_conversion_report(
        {"money_chain": []},
        title="пусто",
    )
    assert text.startswith("💰 Воронка до оплаты\nпусто")
    assert "payment_token_grants" in text


@pytest.mark.asyncio
async def test_admin_conversion_handler_uses_last_30_days_and_renders_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_report(start_utc: str, end_utc: str) -> dict[str, Any]:
        captured["start"] = start_utc
        captured["end"] = end_utc
        return {"money_chain": []}

    def fake_format(report: dict[str, Any], *, title: str) -> str:
        captured["report"] = report
        captured["title"] = title
        return "rendered funnel"

    async def fake_safe_edit(cb, text: str, *, reply_markup=None):
        captured["text"] = text
        captured["reply_markup"] = reply_markup

    monkeypatch.setattr(conversion_handler, "conversion_report", fake_report)
    monkeypatch.setattr(conversion_handler, "format_conversion_report", fake_format)
    monkeypatch.setattr(conversion_handler, "safe_edit", fake_safe_edit)

    keyboard = object()
    result = await conversion_handler.run(
        object(),
        object(),
        SimpleNamespace(staff_kb=keyboard),
        None,
    )

    start = datetime.fromisoformat(captured["start"])
    end = datetime.fromisoformat(captured["end"])
    assert (end - start).days == 30
    assert captured["title"] == "за последние 30 дней"
    assert captured["text"] == "rendered funnel"
    assert captured["reply_markup"] is keyboard
    assert result is True


def test_iso_period_values_are_plain_strings() -> None:
    now = datetime(2099, 8, 31, 12, 0, tzinfo=timezone.utc)
    assert now.isoformat() == "2099-08-31T12:00:00+00:00"
