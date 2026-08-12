from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from handlers.admin_reports import conversion as conversion_handler
from services import admin_payment_path, commercial_funnel_contract
from services import funnel_analytics, funnel_analytics_indexes
from services.db import db


def test_commercial_funnel_contract_is_shared_with_payment_path() -> None:
    assert admin_payment_path._STEP_NAMES is commercial_funnel_contract.PAYMENT_PATH_STEP_NAMES
    assert "demo_sent" in commercial_funnel_contract.COMMERCIAL_STEP_EVENT_NAMES["demo"]
    assert "funnel_demo_work" in commercial_funnel_contract.COMMERCIAL_STEP_EVENT_NAMES["demo"]
    assert "demo_ack" in commercial_funnel_contract.COMMERCIAL_STEP_EVENT_NAMES["listened"]
    assert commercial_funnel_contract.COMMERCIAL_STEP_EVENT_NAMES["checkout"] == ("payment_started",)


def test_commercial_funnel_index_is_selective_and_concurrent() -> None:
    assert len(funnel_analytics_indexes.ONLINE_INDEX_SPECS) == 1
    spec = funnel_analytics_indexes.ONLINE_INDEX_SPECS[0]
    normalized = " ".join(spec.ddl.split())

    assert spec.name == "idx_events_commercial_funnel_v1"
    assert "CREATE INDEX CONCURRENTLY" in normalized
    assert "ON events (name, created_at, user_id)" in normalized
    assert "WHERE name IN" in normalized
    for event_name in commercial_funnel_contract.COMMERCIAL_FUNNEL_EVENT_NAMES:
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
        # Canonical complete journey.
        (-941001, "demo_sent", "2099-08-01T10:00:00+00:00"),
        (-941001, "demo_ack", "2099-08-01T10:03:00+00:00"),
        (-941001, "view_tariffs", "2099-08-01T10:05:00+00:00"),
        (-941001, "payment_started", "2099-08-01T10:07:00+00:00"),
        # Canonical journey that reaches offer but not checkout.
        (-941002, "demo_sent", "2099-08-01T10:01:00+00:00"),
        (-941002, "demo_ack", "2099-08-01T10:04:00+00:00"),
        (-941002, "view_tariffs", "2099-08-01T10:06:00+00:00"),
        # Demo only.
        (-941003, "demo_sent", "2099-08-01T10:02:00+00:00"),
        # Complete journey through aliases already used by admin payment path.
        (-941004, "funnel_demo_work", "2099-08-01T11:00:00+00:00"),
        (-941004, "funnel_demo_ack", "2099-08-01T11:01:00+00:00"),
        (-941004, "funnel_offer_shown", "2099-08-01T11:02:00+00:00"),
        (-941004, "payment_started", "2099-08-01T11:03:00+00:00"),
        # Offer happened before acknowledgement: this must not be promoted into
        # a false ordered conversion even though a later checkout/payment exists.
        (-941005, "demo_sent", "2099-08-01T12:00:00+00:00"),
        (-941005, "view_tariffs", "2099-08-01T12:01:00+00:00"),
        (-941005, "demo_ack", "2099-08-01T12:02:00+00:00"),
        (-941005, "payment_started", "2099-08-01T12:03:00+00:00"),
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
                ("stars", "funnel-paid-4", -941004, "practice_60", 60, None, "2099-08-01T11:04:00+00:00"),
                ("yookassa", "funnel-paid-5", -941005, "practice_60", 60, None, "2099-08-01T12:04:00+00:00"),
            ],
        )
        conn.commit()


def test_conversion_report_uses_ordered_aliases_and_authoritative_paid_users() -> None:
    _seed_money_funnel()

    report = funnel_analytics.conversion_report(
        "2099-08-01T00:00:00+00:00",
        "2099-09-01T00:00:00+00:00",
    )

    # Raw compatibility counts stay event-specific.
    assert report["counts"]["demo_sent"] == 4
    assert report["counts"]["demo_ack"] == 3
    assert report["counts"]["view_tariffs"] == 3
    assert report["counts"]["payment_started"] == 3

    # Strict money chain is alias-aware, ordered, and therefore monotonic.
    chain = report["money_chain"]
    assert [item["step"] for item in chain] == ["demo", "listened", "offer", "checkout", "paid"]
    assert [item["users"] for item in chain] == [5, 4, 3, 2, 2]
    assert chain[1]["from_prev_pct"] == pytest.approx(80.0)
    assert chain[2]["from_prev_pct"] == pytest.approx(75.0)
    assert chain[3]["from_prev_pct"] == pytest.approx(66.7)
    assert chain[4]["from_prev_pct"] == pytest.approx(100.0)

    # Three users paid in the period, but one did not traverse the ordered demo
    # chain. Repeat grants for the same user still count that user once.
    assert report["paid_users"] == 3
    assert report["strict_paid_users"] == 2

    text = funnel_analytics.format_conversion_report(report, title="контрольный период")
    assert "💰 Воронка до оплаты" in text
    assert "контрольный период" in text
    assert "Создан checkout: 2" in text
    assert "Оплатили пакет: 2" in text
    assert "Всего плативших за пакеты в периоде: 3" in text
    assert "остальные оплаты пришли другим путём" in text
    assert "Самый слабый последовательный переход: Создан checkout — 66.7%" in text
    assert "payment_token_grants" in text


def test_strict_money_chain_counts_repeat_purchase_after_new_checkout() -> None:
    user_id = -941101
    windows = {
        "bounded": ("2099-08-10T10:00:00+00:00", "2099-08-10T11:00:00+00:00"),
        "start_only": ("2099-08-10T10:00:00+00:00", None),
        "end_only": (None, "2099-08-10T11:00:00+00:00"),
        "all": (None, None),
    }
    baseline = {
        name: funnel_analytics._strict_money_counts(start, end)
        for name, (start, end) in windows.items()
    }

    with db() as conn:
        conn.executemany(
            "INSERT INTO events(user_id, name, meta, created_at) VALUES(?,?,?,?)",
            [
                (user_id, "demo_sent", "{}", "2099-08-10T10:00:00+00:00"),
                (user_id, "demo_ack", "{}", "2099-08-10T10:02:00+00:00"),
                (user_id, "view_tariffs", "{}", "2099-08-10T10:03:00+00:00"),
                (user_id, "payment_started", "{}", "2099-08-10T10:04:00+00:00"),
            ],
        )
        # The first successful grant is before this checkout. The later grant is
        # the repeat purchase that closes the new checkout and must be counted.
        conn.executemany(
            """
            INSERT INTO payment_token_grants(
                provider, provider_payment_id, user_id, package_id,
                tokens_granted, ledger_id, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """.strip(),
            [
                ("yookassa", "funnel-repeat-before-checkout", user_id, "practice_60", 60, None, "2099-08-10T10:01:00+00:00"),
                ("yookassa", "funnel-repeat-after-checkout", user_id, "practice_60", 60, None, "2099-08-10T10:05:00+00:00"),
            ],
        )
        conn.commit()

    for name, (start, end) in windows.items():
        current = funnel_analytics._strict_money_counts(start, end)
        before = baseline[name]
        for key in ("demo", "listened", "offer", "checkout", "paid", "paid_total"):
            assert current[key] == before[key] + 1


def test_conversion_breakdown_uses_aliases_kind_and_daypart() -> None:
    with db() as conn:
        conn.executemany(
            "INSERT INTO events(user_id, name, meta, created_at) VALUES(?,?,?,?)",
            [
                (-941201, "funnel_demo_work", '{"kind":"work"}', "2099-08-20T06:00:00+00:00"),
                (-941201, "funnel_demo_ack", '{"kind":"work"}', "2099-08-20T06:01:00+00:00"),
                (-941201, "funnel_offer_shown", "{}", "2099-08-20T06:02:00+00:00"),
                (-941201, "funnel_pay_success", "{}", "2099-08-20T06:03:00+00:00"),
                (-941202, "funnel_demo_home", '{"kind":"home"}', "2099-08-20T13:00:00+00:00"),
                (-941202, "audio_listened", '{"kind":"home"}', "2099-08-20T13:01:00+00:00"),
                (-941202, "sub_menu", "{}", "2099-08-20T13:02:00+00:00"),
                (-941202, "sub_paid", "{}", "2099-08-20T13:03:00+00:00"),
                # Malformed meta must degrade to unknown instead of breaking the report.
                (-941203, "demo_sent", "{bad-json", "2099-08-20T18:00:00+00:00"),
                (-941203, "demo_ack", "{}", "2099-08-20T18:01:00+00:00"),
            ],
        )
        conn.commit()

    report = funnel_analytics.conversion_breakdown(
        "2099-08-20T00:00:00+00:00",
        "2099-08-21T00:00:00+00:00",
        tz_name="Europe/Moscow",
    )

    assert report["by_kind"]["work"] == {
        "demo_sent": 1,
        "demo_ack": 1,
        "view_tariffs": 1,
        "sub_paid": 1,
    }
    assert report["by_kind"]["home"] == {
        "demo_sent": 1,
        "demo_ack": 1,
        "view_tariffs": 1,
        "sub_paid": 1,
    }
    assert report["by_kind"]["unknown"]["demo_sent"] == 1
    assert report["by_kind"]["unknown"]["demo_ack"] == 1
    assert report["by_daypart"]["утро"]["sub_paid"] == 1
    assert report["by_daypart"]["день"]["sub_paid"] == 1
    assert report["by_daypart"]["вечер"]["demo_ack"] == 1
    assert funnel_analytics._breakdown_step("not-a-commercial-event") is None


def test_paid_user_count_respects_period_boundaries() -> None:
    windows = {
        "bounded": ("2099-08-10T00:00:00+00:00", "2099-09-01T00:00:00+00:00", 1),
        "start_only": ("2099-08-10T00:00:00+00:00", None, 2),
        "end_only": (None, "2099-09-01T00:00:00+00:00", 2),
        "all": (None, None, 3),
    }
    baseline = {
        name: funnel_analytics._paid_user_count(start, end)
        for name, (start, end, _delta) in windows.items()
    }

    with db() as conn:
        conn.executemany(
            """
            INSERT INTO payment_token_grants(
                provider, provider_payment_id, user_id, package_id,
                tokens_granted, ledger_id, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """.strip(),
            [
                ("yookassa", "paid-period-before", -941301, "practice_60", 60, None, "2099-08-01T00:00:00+00:00"),
                ("yookassa", "paid-period-inside", -941302, "practice_60", 60, None, "2099-08-15T00:00:00+00:00"),
                ("stars", "paid-period-after", -941303, "practice_60", 60, None, "2099-09-15T00:00:00+00:00"),
            ],
        )
        conn.commit()

    for name, (start, end, delta) in windows.items():
        assert funnel_analytics._paid_user_count(start, end) == baseline[name] + delta


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


def test_commercial_counts_cover_open_periods_without_extra_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, tuple[Any, ...]]] = []

    class FakeConnection:
        def execute(self, sql: str, params: tuple[Any, ...]):
            executed.append((sql, params))
            return self

        def fetchall(self):
            return []

    @contextmanager
    def fake_db():
        yield FakeConnection()

    monkeypatch.setattr(funnel_analytics, "db", fake_db)

    assert funnel_analytics._commercial_counts(["demo_sent"], "2099-08-01T00:00:00+00:00", None) == {"demo_sent": 0}
    assert funnel_analytics._commercial_counts(["demo_sent"], None, "2099-09-01T00:00:00+00:00") == {"demo_sent": 0}
    assert funnel_analytics._commercial_counts(["demo_sent"], None, None) == {"demo_sent": 0}
    assert len(executed) == 3
    assert "created_at >= ?" in executed[0][0] and "created_at < ?" not in executed[0][0]
    assert "created_at < ?" in executed[1][0] and "created_at >= ?" not in executed[1][0]
    assert "created_at >= ?" not in executed[2][0] and "created_at < ?" not in executed[2][0]


def test_rate_chain_and_empty_format_are_stable() -> None:
    chain = funnel_analytics._rate_chain([("a", 0), ("b", 0)])
    assert chain[0]["from_prev_pct"] is None
    assert chain[1]["from_prev_pct"] == 0.0
    assert chain[1]["dropped_from_prev"] == 0

    text = funnel_analytics.format_conversion_report(
        {"money_chain": [], "paid_users": 0, "strict_paid_users": 0},
        title="пусто",
    )
    assert text.startswith("💰 Воронка до оплаты\nпусто")
    assert "payment_token_grants" in text


def test_format_conversion_report_handles_lossless_strict_chain() -> None:
    chain = funnel_analytics._rate_chain(
        [("demo", 2), ("listened", 2), ("offer", 2), ("checkout", 2), ("paid", 2)]
    )
    text = funnel_analytics.format_conversion_report(
        {"money_chain": chain, "paid_users": 2, "strict_paid_users": 2},
        title="без потерь",
    )
    assert "Всего плативших за пакеты в периоде: 2" in text
    assert "остальные оплаты пришли другим путём" not in text
    assert "Явной точки потери в строгой демо-цепочке пока нет" in text


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
