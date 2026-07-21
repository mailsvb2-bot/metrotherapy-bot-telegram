from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from services.messenger import text_ui as ui


class Item:
    def __init__(self, anchor: int, title: str) -> None:
        self.anchor = anchor
        self.title = title


class Snapshot:
    def __init__(
        self,
        *,
        pending: Item | None = None,
        next_item: Item | None = None,
        last_anchor: int | None = None,
        last_title: str | None = None,
        pending_platform: str | None = None,
        last_platform: str | None = None,
    ) -> None:
        self.pending_item = pending
        self.next_item = next_item
        self.last_anchor = last_anchor
        self.last_title = last_title
        self.pending_platform = pending_platform
        self.last_platform = last_platform


def test_basic_text_renderers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "get_preface", lambda user_id, context: f"hello:{user_id}:{context}\n")
    assert "hello:7:menu" in ui._menu_text(7)
    assert "Главное меню" in ui._menu_text(7)
    assert "-10" in ui._score_scale_text()
    assert "Подсказка" in ui._help_text()
    assert "Бесплатная практика" in ui._demo_text()
    assert "Полный маршрут" in ui._full_route_text(7)
    assert "Погода" in ui._weather_text()
    assert "название города" in ui._weather_city_prompt_text()
    assert "Дорога на работу" in ui._text_time_prompt("work")
    assert "Дорога домой" in ui._text_time_prompt("home")
    assert "1 — хуже" in ui._state_rate_prompt_text()


def test_settings_text_deduplicates_linked_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ui,
        "get_channel_snapshot",
        lambda _uid: {
            "preferred_platform": "max",
            "identities": [
                {"platform": "max"},
                {"platform": "max"},
                {"platform": "vk"},
            ],
        },
    )
    monkeypatch.setattr(ui, "platform_title", lambda value: str(value or "Telegram").upper())
    monkeypatch.setattr(ui, "describe_delivery_preferences", lambda _uid: "delivery")
    text = ui._settings_text(9)
    assert "MAX" in text
    assert "MAX, VK" in text
    assert "delivery" in text

    monkeypatch.setattr(
        ui,
        "get_channel_snapshot",
        lambda _uid: {"preferred_platform": None, "identities": []},
    )
    assert "пока нет" in ui._settings_text(9)


def test_share_payment_gift_and_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ui,
        "build_messenger_targets",
        lambda _uid: [
            {"title": "Telegram", "url": "https://t.example"},
            {"title": "Сайт", "url": "https://site.example"},
        ],
    )
    share = ui._share_text(3)
    assert "Дополнительные ссылки" in share
    assert "https://t.example" in share

    monkeypatch.setattr(ui, "build_messenger_targets", lambda _uid: [])
    assert "Дополнительные ссылки" not in ui._share_text(3)

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ui, "normalize_platform", lambda value: f"norm:{value}")
    monkeypatch.setattr(
        ui,
        "package_payment_text",
        lambda **kwargs: calls.append(("pay", kwargs)) or "PAY",
    )
    monkeypatch.setattr(
        ui,
        "gift_package_text",
        lambda **kwargs: calls.append(("gift", kwargs)) or "GIFT",
    )
    monkeypatch.setattr(ui, "gift_recipient_prompt_text", lambda: "RECIPIENT")
    monkeypatch.setattr(ui, "set_pending", lambda *args: calls.append(("pending", *args)))

    assert ui._payment_text(5, platform="vk", external_user_id="x") == "PAY"
    assert ui._gift_text(5, platform="max", external_user_id="y", recipient_hint="Ann") == "GIFT"
    reply = ui._start_gift_recipient_flow(5)
    assert reply.text == "RECIPIENT"
    assert ("pending", 5, "gift_recipient", {}) in calls
    after = ui._gift_payment_after_recipient(5, platform="vk", external_user_id="x", recipient_hint="Bob")
    assert after.text == "GIFT"
    assert any(call[0] == "pay" and call[1]["platform"] == "norm:vk" for call in calls)
    assert any(call[0] == "gift" and call[1]["recipient_hint"] == "Bob" for call in calls)


def test_switch_text_with_and_without_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "issue_bridge_token", lambda _uid: "token")
    monkeypatch.setattr(ui, "build_switch_targets", lambda _token: [])
    assert "пока не настроено" in ui._switch_text(1)

    monkeypatch.setattr(
        ui,
        "build_switch_targets",
        lambda _token: [{"title": "MAX", "url": "https://max.example"}],
    )
    text = ui._switch_text(1)
    assert "https://max.example" in text
    assert "без потери прогресса" in text


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (Snapshot(pending=Item(2, "Pending")), "уже выдано"),
        (Snapshot(next_item=Item(1, "First")), "Следующим будет №1"),
        (Snapshot(last_anchor=3, last_title="Last", next_item=Item(4, "Next")), "Вы уже дошли до №3"),
        (Snapshot(last_anchor=5, last_title="Last"), "дослушана до конца"),
    ],
)
def test_bridge_linked_text(monkeypatch: pytest.MonkeyPatch, snapshot: Snapshot, expected: str) -> None:
    monkeypatch.setattr(ui, "get_progress_snapshot", lambda _uid: snapshot)
    monkeypatch.setattr(ui, "platform_title", lambda value: str(value).upper())
    text = ui._bridge_linked_text(4, "vk")
    assert "VK привязан" in text
    assert expected in text
    assert ui._should_auto_resume_after_bridge(4) is bool(snapshot.pending_item or snapshot.next_item)


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (Snapshot(), "Аудиосерия пока не найдена"),
        (Snapshot(next_item=Item(1, "First")), "ещё не запускали"),
        (
            Snapshot(next_item=Item(2, "Next"), pending=Item(1, "Pending"), pending_platform="max"),
            "ещё не подтверждено",
        ),
        (
            Snapshot(last_anchor=3, last_title="Last", next_item=Item(4, "Next"), last_platform="vk"),
            "Последнее подтверждённое аудио: №3",
        ),
        (Snapshot(last_anchor=5, last_title="Last", last_platform="telegram"), "дослушана до конца"),
    ],
)
def test_progress_text_states(monkeypatch: pytest.MonkeyPatch, snapshot: Snapshot, expected: str) -> None:
    monkeypatch.setattr(ui, "get_progress_snapshot", lambda _uid: snapshot)
    monkeypatch.setattr(ui, "platform_title", lambda value: str(value or "unknown").upper())
    assert expected in ui._progress_text(8)


def test_history_text_empty_and_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "get_recent_audio_timeline", lambda *_args, **_kwargs: [])
    assert "пока пуста" in ui._history_text(1)

    events = [
        SimpleNamespace(
            event_type="access_confirmed",
            created_at="2026-07-21",
            anchor=2,
            title="Calm",
            platform="vk",
        ),
        SimpleNamespace(
            event_type="custom_event",
            created_at="later",
            anchor=None,
            title="",
            platform=None,
        ),
    ]
    monkeypatch.setattr(ui, "get_recent_audio_timeline", lambda *_args, **_kwargs: events)
    monkeypatch.setattr(ui, "platform_title", lambda value: str(value).upper())
    text = ui._history_text(1)
    assert "аудио открыто" in text
    assert "№2" in text
    assert "(VK)" in text
    assert "custom_event" in text


def test_parse_hhmm_for_reminder() -> None:
    assert ui._parse_hhmm_for_reminder("08:30") == (8, 30)
    assert ui._parse_hhmm_for_reminder("8:5") == (8, 5)
    for raw in ("", "8", "x:10", "24:00", "10:60"):
        assert ui._parse_hhmm_for_reminder(raw) is None


def test_save_text_time_all_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class Conn:
        def execute(self, sql: str, params: tuple[Any, ...]):
            calls.append((sql, params))
            return self

    @contextmanager
    def fake_db() -> Iterator[Conn]:
        yield Conn()

    monkeypatch.setattr(ui, "db", fake_db)
    assert "формате HH:MM" in ui._save_text_time(7, "work", "bad")
    assert "Дорога на работу" in ui._save_text_time(7, "work", "8:05")
    assert "Дорога домой" in ui._save_text_time(7, "home", "19:30")
    assert "Не понял" in ui._save_text_time(7, "other", "10:00")
    assert any("work_time" in sql for sql, _ in calls)
    assert any("home_time" in sql for sql, _ in calls)


def test_ref_bonus_and_delivery_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "paid_referrals_count", lambda _uid: 3)
    monkeypatch.setattr(ui, "gift_grants_count", lambda _uid: 2)
    monkeypatch.setattr(ui, "gift_days_granted", lambda _uid: 14)
    monkeypatch.setattr(
        ui,
        "compute_bonus_stats",
        lambda _uid: SimpleNamespace(earned_days=20, used_days=4, remaining_days=16),
    )
    text = ui._ref_bonus_text(5)
    assert "3 человек" in text
    assert "остаток: 16" in text

    monkeypatch.setattr(ui, "describe_delivery_preferences", lambda _uid: "prefs")
    assert "prefs" in ui._delivery_channels_text(5)
    assert "утренних" in ui._delivery_slot_text(5, "morning")
    assert "вечерних" in ui._delivery_slot_text(5, "evening")


def test_save_state_rate_and_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "распознать" in ui._save_state_rate_text(1, "x")
    assert "от 1 до 10" in ui._save_state_rate_text(1, "0")
    monkeypatch.setattr(ui, "add_rating", lambda *_args: False)
    assert "Не удалось сохранить" in ui._save_state_rate_text(1, "5")
    monkeypatch.setattr(ui, "add_rating", lambda *_args: True)
    assert "5/10" in ui._save_state_rate_text(1, "5")

    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(ui, "set_preferred_platform", lambda uid, platform: calls.append((uid, platform)))
    monkeypatch.setattr(ui, "platform_title", lambda value: str(value).upper())
    assert "VK" in ui._platform_changed_text(1, "vk")
    assert calls == [(1, "vk")]


def test_pending_score_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        def __init__(self, row: Any) -> None:
            self.row = row

        def fetchone(self) -> Any:
            return self.row

    class Conn:
        def __init__(self, row: Any) -> None:
            self.row = row

        def execute(self, *_args: Any, **_kwargs: Any) -> Result:
            return Result(self.row)

    @contextmanager
    def fake_db_row() -> Iterator[Conn]:
        yield Conn({"stage": "post"})

    monkeypatch.setattr(ui, "db", fake_db_row)
    assert ui._pending_score_stage(1) == "post"

    @contextmanager
    def fake_db_none() -> Iterator[Conn]:
        yield Conn(None)

    monkeypatch.setattr(ui, "db", fake_db_none)
    assert ui._pending_score_stage(1) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ("menu", None)),
        ("mood:done:7", ("done", "7")),
        ("mood:pre:7:4", ("pre_score", "4")),
        ("mood:post:7:-2", ("post_score", "-2")),
        ("state:rate", ("state_rate_menu", None)),
        ("state:rate:8", ("state_rate_save", "8")),
        ("state:yesterday", ("state_period", "yesterday")),
        ("/start bridge-token", ("start", "bridge-token")),
        ("help", ("help", None)),
        ("settings", ("settings", None)),
        ("share", ("share", None)),
        ("switch", ("switch", None)),
        ("continue", ("continue", None)),
        ("повторить аудио", ("repeat_audio", None)),
        ("готово", ("done", None)),
        ("progress", ("progress", None)),
        ("history", ("history", None)),
        ("sub:menu", ("pay", None)),
        ("gift:menu", ("gift", None)),
        ("weather:show", ("weather", None)),
        ("settings:time:work", ("settings_time", "work")),
        ("settings:delivery:slot:set:morning:auto", ("channel", "morning auto")),
        ("timezone Europe/Amsterdam", ("timezone", "Europe/Amsterdam")),
        ("quiet off", ("quiet", "off")),
        ("channel evening vk", ("channel", "evening vk")),
        ("1", ("demo_work", None)),
        ("2", ("demo_home", None)),
        ("полный маршрут", ("full", None)),
        ("изменить город", ("weather_city", None)),
        ("оплатить", ("pay", None)),
        ("подарить", ("gift", None)),
        ("/platform max", ("platform", "max")),
        ("totally unknown", ("menu", None)),
    ],
)
def test_parse_command_matrix(monkeypatch: pytest.MonkeyPatch, text: str, expected: tuple[str, str | None]) -> None:
    monkeypatch.setattr(ui, "normalize_menu_command", lambda _raw: None)
    assert ui._parse_command(text) == expected


def test_parse_command_menu_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "normalize_menu_command", lambda raw: "start" if raw == "button-start" else "weather")
    assert ui._parse_command("button-start") == ("menu", None)
    assert ui._parse_command("button-weather") == ("weather", None)
