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


class Pending:
    def __init__(self, kind: str, data: dict[str, Any] | None = None) -> None:
        self.kind = kind
        self.data = data or {}


def install_entry(monkeypatch: pytest.MonkeyPatch, *, linked: bool = False) -> None:
    monkeypatch.setattr(ui, "normalize_platform", lambda value: str(value).lower())
    monkeypatch.setattr(ui, "parse_score_text", lambda _text: None)
    monkeypatch.setattr(
        ui,
        "register_user_entry",
        lambda *_args, **_kwargs: SimpleNamespace(user_id=91, linked_via_bridge=linked),
    )
    monkeypatch.setattr(ui, "peek_pending", lambda _uid: None)
    monkeypatch.setattr(ui, "pop_pending", lambda _uid: None)
    monkeypatch.setattr(ui, "get_preface", lambda *_args, **_kwargs: "")


def replies_for(monkeypatch: pytest.MonkeyPatch, text: str, *, platform: str = "vk") -> list[ui.MessengerReply]:
    _uid, replies = ui.handle_incoming_text(
        5,
        platform=platform,
        external_user_id="external",
        text=text,
        username="user",
        display_name="Name",
        first_name="First",
    )
    return replies


def test_kind_for_audio_item() -> None:
    assert ui._kind_for_audio_item(Item(1, "Morning calm")) == "work"
    assert ui._kind_for_audio_item(Item(2, "Neutral")) == "home"
    assert ui._kind_for_audio_item(Item(3, "Вечер дома")) == "home"
    assert ui._kind_for_audio_item(SimpleNamespace(anchor="bad", title="Unknown")) == "work"
    assert ui._kind_for_audio_item(None) == "work"


def test_start_pre_audio_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "get_progress_snapshot", lambda _uid: Snapshot())
    reply = ui._start_vk_pre_audio_session(1, kind="work")
    assert "Все доступные аудио" in reply.text

    monkeypatch.setattr(
        ui,
        "get_progress_snapshot",
        lambda _uid: Snapshot(pending=Item(7, "Pending"), next_item=Item(8, "Next")),
    )
    calls: list[tuple[Any, ...]] = []

    def create(*args: Any, **kwargs: Any) -> int:
        calls.append((args, kwargs))
        return 44

    monkeypatch.setattr(ui, "create_session", create)
    reply = ui._start_vk_pre_audio_session(1, kind="home")
    assert reply.meta == {"vk_keyboard": "score_scale", "session_id": "44", "stage": "pre"}
    assert "вечерней" in reply.text
    assert calls[0][1]["anchor_id"] == 7
    assert calls[0][1]["slot"] == "evening"


def test_continue_and_repeat_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "get_progress_snapshot", lambda _uid: Snapshot(pending=Item(1, "Pending")))
    assert ui._continue_vk_audio_session(1).kind == "next_audio"
    assert ui._repeat_last_vk_audio(1).meta == {"replay": "1"}

    monkeypatch.setattr(ui, "get_progress_snapshot", lambda _uid: Snapshot(next_item=Item(1, "First")))
    first = ui._continue_vk_audio_session(1)
    assert "Бесплатная практика" in first.text
    assert first.meta["vk_keyboard"] == "demo_kind"

    monkeypatch.setattr(
        ui,
        "get_progress_snapshot",
        lambda _uid: Snapshot(last_anchor=1, last_title="One", next_item=Item(2, "Even")),
    )
    monkeypatch.setattr(ui, "_start_vk_pre_audio_session", lambda _uid, kind: ui.MessengerReply(text=f"kind:{kind}"))
    assert ui._continue_vk_audio_session(1).text == "kind:home"

    monkeypatch.setattr(ui, "get_progress_snapshot", lambda _uid: Snapshot(last_anchor=9, last_title="End"))
    assert "Все доступные аудио" in ui._continue_vk_audio_session(1).text
    monkeypatch.setattr(ui, "get_audio_item_by_anchor", lambda anchor: Item(anchor, "Last"))
    assert ui._repeat_last_vk_audio(1).meta["replay"] == "1"

    monkeypatch.setattr(ui, "get_progress_snapshot", lambda _uid: Snapshot())
    assert ui._repeat_last_vk_audio(1).meta["vk_keyboard"] == "demo_kind"


def test_schedule_continue_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    class Row(dict):
        pass

    class Result:
        def fetchone(self) -> Row:
            return Row(work_time="07:15")

    class Conn:
        def execute(self, *_args: Any, **_kwargs: Any) -> Result:
            return Result()

    @contextmanager
    def fake_db() -> Iterator[Conn]:
        yield Conn()

    jobs: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ui, "db", fake_db)
    monkeypatch.setattr(ui.settings, "TIMEZONE", "Europe/Amsterdam")
    monkeypatch.setattr(ui.settings, "MORNING_TIME", "08:30")
    monkeypatch.setattr(ui, "cancel_jobs", lambda *args, **kwargs: jobs.append(("cancel", args, kwargs)))
    monkeypatch.setattr(ui, "add_job", lambda *args, **kwargs: jobs.append(("add", args, kwargs)))
    text = ui._schedule_continue_tomorrow_text(3)
    assert "07:15" in text
    assert any(call[0] == "cancel" for call in jobs)
    add = next(call for call in jobs if call[0] == "add")
    assert add[1][1] == "remind_continue"
    assert add[1][3]["hhmm"] == "07:15"


def test_handle_payment_and_telegram_shortcuts(monkeypatch: pytest.MonkeyPatch) -> None:
    install_entry(monkeypatch)
    monkeypatch.setattr(ui, "_payment_text", lambda *_args, **_kwargs: "PAY")
    assert replies_for(monkeypatch, "pay")[0].text == "PAY"
    assert replies_for(monkeypatch, "continue", platform="telegram")[0].kind == "next_audio"

    monkeypatch.setattr(ui, "confirm_pending_audio_delivery", lambda *_args, **_kwargs: None)
    assert "нет аудио" in replies_for(monkeypatch, "done", platform="telegram")[0].text

    monkeypatch.setattr(
        ui,
        "confirm_pending_audio_delivery",
        lambda *_args, **_kwargs: Item(2, "Done"),
    )
    done = replies_for(monkeypatch, "done", platform="telegram")
    assert "№2" in done[0].text
    assert done[-1].kind == "next_audio"


def test_handle_pending_input_flows(monkeypatch: pytest.MonkeyPatch) -> None:
    install_entry(monkeypatch)
    pending: list[Pending | None] = [Pending("set_time", {"slot": "home"})]
    monkeypatch.setattr(ui, "peek_pending", lambda _uid: pending[0])
    monkeypatch.setattr(ui, "pop_pending", lambda _uid: pending.pop(0))
    monkeypatch.setattr(ui, "_save_text_time", lambda uid, slot, value: f"{uid}:{slot}:{value}")
    assert replies_for(monkeypatch, "19:30")[0].text == "91:home:19:30"

    pending[:] = [Pending("weather_city")]
    reply = replies_for(monkeypatch, "Amsterdam")[0]
    assert reply.kind == "weather_set_city"
    assert reply.meta["city"] == "Amsterdam"

    pending[:] = [Pending("gift_recipient")]
    monkeypatch.setattr(
        ui,
        "_gift_payment_after_recipient",
        lambda *_args, **kwargs: ui.MessengerReply(text=f"gift:{kwargs['recipient_hint']}"),
    )
    assert replies_for(monkeypatch, "Иван")[0].text == "gift:Иван"

    for kind in ("set_time", "weather_city", "gift_recipient"):
        pending[:] = [Pending(kind, {"slot": "work"})]
        replies_for(monkeypatch, "menu")
        assert pending == []


def test_handle_menu_bridge_and_simple_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    install_entry(monkeypatch, linked=True)
    monkeypatch.setattr(ui, "_bridge_linked_text", lambda *_args: "LINKED")
    monkeypatch.setattr(ui, "_should_auto_resume_after_bridge", lambda _uid: True)
    linked = replies_for(monkeypatch, "start")
    assert linked[0].text == "LINKED"
    assert linked[1].kind == "next_audio"

    install_entry(monkeypatch, linked=False)
    monkeypatch.setattr(ui, "_menu_text", lambda _uid: "MENU")
    monkeypatch.setattr(ui, "_help_text", lambda: "HELP")
    monkeypatch.setattr(ui, "_settings_text", lambda _uid: "SETTINGS")
    monkeypatch.setattr(ui, "_demo_text", lambda: "DEMO")
    monkeypatch.setattr(ui, "_share_text", lambda _uid: "SHARE")
    monkeypatch.setattr(ui, "_switch_text", lambda _uid: "SWITCH")
    monkeypatch.setattr(ui, "_repeat_last_vk_audio", lambda _uid: ui.MessengerReply(text="REPEAT"))
    monkeypatch.setattr(ui, "_continue_vk_audio_session", lambda _uid: ui.MessengerReply(text="CONTINUE"))
    monkeypatch.setattr(ui, "_start_vk_pre_audio_session", lambda _uid, kind: ui.MessengerReply(text=f"DEMO:{kind}"))

    expected = {
        "menu": "MENU",
        "help": "HELP",
        "settings": "SETTINGS",
        "demo": "DEMO",
        "share": "SHARE",
        "switch": "SWITCH",
        "repeat": "REPEAT",
        "continue": "CONTINUE",
        "1": "DEMO:work",
        "2": "DEMO:home",
    }
    for command, text in expected.items():
        assert replies_for(monkeypatch, command)[0].text == text


def test_handle_scores_done_progress_and_history(monkeypatch: pytest.MonkeyPatch) -> None:
    install_entry(monkeypatch)
    monkeypatch.setattr(ui, "parse_score_text", lambda text: int(text) if text in {"3", "4"} else None)
    pending = Pending("mood_post_score")
    monkeypatch.setattr(ui, "peek_pending", lambda _uid: pending)
    monkeypatch.setattr(ui, "pop_pending", lambda _uid: pending)
    assert replies_for(monkeypatch, "3")[0].kind == "auto_post_score"

    monkeypatch.setattr(ui, "peek_pending", lambda _uid: None)
    monkeypatch.setattr(ui, "_pending_score_stage", lambda _uid: "pre")
    assert replies_for(monkeypatch, "4")[0].kind == "auto_pre_score"

    monkeypatch.setattr(ui, "find_pending_post_session_id", lambda _uid: None)
    monkeypatch.setattr(ui, "confirm_pending_audio_delivery", lambda *_args, **_kwargs: None)
    assert "нет аудио" in replies_for(monkeypatch, "done")[0].text

    monkeypatch.setattr(ui, "find_pending_post_session_id", lambda _uid: 12)
    stored: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ui, "set_pending", lambda *args: stored.append(args))
    done = replies_for(monkeypatch, "done")[0]
    assert done.meta["stage"] == "post"
    assert stored[-1][1] == "mood_post_score"

    monkeypatch.setattr(ui, "confirm_pending_audio_delivery", lambda *_args, **_kwargs: Item(5, "Track"))
    assert "№5" in replies_for(monkeypatch, "done")[0].text

    monkeypatch.setattr(ui, "_progress_text", lambda _uid: "PROGRESS")
    progress = replies_for(monkeypatch, "progress")
    assert [reply.kind for reply in progress] == ["text", "progress_chart"]
    monkeypatch.setattr(ui, "_history_text", lambda _uid: "HISTORY")
    assert replies_for(monkeypatch, "history")[0].text == "HISTORY"


def test_handle_settings_and_delivery_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    install_entry(monkeypatch)
    stored: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ui, "set_pending", lambda *args: stored.append(args))
    monkeypatch.setattr(ui, "_text_time_prompt", lambda slot: f"TIME:{slot}")
    monkeypatch.setattr(ui, "_ref_bonus_text", lambda _uid: "BONUS")
    monkeypatch.setattr(ui, "_delivery_channels_text", lambda _uid: "CHANNELS")
    monkeypatch.setattr(ui, "_delivery_slot_text", lambda _uid, slot: f"SLOT:{slot}")

    assert replies_for(monkeypatch, "settings:time:home")[0].text == "TIME:home"
    assert stored[-1][1] == "set_time"
    assert replies_for(monkeypatch, "settings:ref")[0].text == "BONUS"
    assert replies_for(monkeypatch, "settings:delivery:channels")[0].text == "CHANNELS"
    assert replies_for(monkeypatch, "settings:delivery:slot:evening")[0].text == "SLOT:evening"

    monkeypatch.setattr(ui, "describe_delivery_preferences", lambda _uid: "prefs")
    decisions = {
        "morning": SimpleNamespace(resolved_channel="max", fallback_used=True),
        "evening": SimpleNamespace(resolved_channel="vk", fallback_used=False),
    }
    monkeypatch.setattr(ui, "build_delivery_policy_decision", lambda _uid, slot: decisions[slot])
    monkeypatch.setattr(ui, "platform_title", lambda value: str(value).upper())
    text = replies_for(monkeypatch, "time")[0].text
    assert "MAX (fallback)" in text
    assert "VK" in text


def test_handle_timezone_quiet_channel_platform_and_weather(monkeypatch: pytest.MonkeyPatch) -> None:
    install_entry(monkeypatch)
    monkeypatch.setattr(ui, "describe_delivery_preferences", lambda _uid: "prefs")
    monkeypatch.setattr(ui, "set_user_timezone", lambda _uid, value: value)
    assert "Europe/Amsterdam" in replies_for(monkeypatch, "timezone Europe/Amsterdam")[0].text
    monkeypatch.setattr(ui, "set_user_timezone", lambda *_args: (_ for _ in ()).throw(ValueError("bad")))
    assert "корректный" in replies_for(monkeypatch, "timezone Mars/Olympus")[0].text

    cleared: list[int] = []
    monkeypatch.setattr(ui, "clear_quiet_hours", lambda uid: cleared.append(uid))
    assert "выключены" in replies_for(monkeypatch, "quiet off")[0].text
    assert cleared == [91]
    assert "формате" in replies_for(monkeypatch, "quiet bad")[0].text
    monkeypatch.setattr(ui, "set_quiet_hours", lambda _uid, start, end: (start, end))
    assert "22:00-08:00" in replies_for(monkeypatch, "quiet 22:00-08:00")[0].text
    monkeypatch.setattr(ui, "set_quiet_hours", lambda *_args: (_ for _ in ()).throw(KeyError("bad")))
    assert "Не смог распознать" in replies_for(monkeypatch, "quiet 25:00-08:00")[0].text

    monkeypatch.setattr(ui, "set_slot_channel", lambda _uid, _slot, value: value)
    monkeypatch.setattr(
        ui,
        "build_delivery_policy_decision",
        lambda *_args: SimpleNamespace(resolved_channel="telegram", fallback_used=True),
    )
    monkeypatch.setattr(ui, "platform_title", lambda value: str(value).upper())
    assert "fallback" in replies_for(monkeypatch, "channel morning vk")[0].text
    assert "авто" in replies_for(monkeypatch, "channel evening auto")[0].text
    assert "Используйте" in replies_for(monkeypatch, "channel bad")[0].text
    assert "Допустимые" in replies_for(monkeypatch, "channel morning sms")[0].text

    monkeypatch.setattr(ui, "_platform_changed_text", lambda _uid, platform: f"PLATFORM:{platform}")
    monkeypatch.setattr(ui, "_settings_text", lambda _uid: "SETTINGS")
    assert replies_for(monkeypatch, "/platform vk")[0].text == "PLATFORM:vk"
    assert "Используйте" in replies_for(monkeypatch, "/platform sms")[0].text

    monkeypatch.setattr(ui, "_full_route_text", lambda _uid: "FULL")
    assert replies_for(monkeypatch, "full")[0].text == "FULL"
    weather = replies_for(monkeypatch, "weather")
    assert weather[0].meta["vk_keyboard"] == "weather"
    assert weather[1].kind == "weather_show"
    city = replies_for(monkeypatch, "weather_city")[0]
    assert city.meta["vk_keyboard"] == "weather_city"


def test_handle_state_rating_period_and_reminder(monkeypatch: pytest.MonkeyPatch) -> None:
    install_entry(monkeypatch)
    monkeypatch.setattr(ui, "_save_state_rate_text", lambda _uid, value: f"RATE:{value}")
    assert replies_for(monkeypatch, "state:rate")[0].meta["vk_keyboard"] == "state_rate"
    assert replies_for(monkeypatch, "state:rate:7")[0].text == "RATE:7"

    monkeypatch.setattr(ui, "_progress_text", lambda _uid: "PROGRESS")
    period = replies_for(monkeypatch, "state:all")
    assert period[0].meta["period"] == "all"
    assert period[1].meta["period"] == "all"

    monkeypatch.setattr(ui, "_schedule_continue_tomorrow_text", lambda _uid: "REMINDER")
    assert replies_for(monkeypatch, "remind_continue_tomorrow")[0].text == "REMINDER"


def test_handle_falls_back_to_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    install_entry(monkeypatch)
    monkeypatch.setattr(ui, "_menu_text", lambda _uid: "MENU")
    assert replies_for(monkeypatch, "unknown command")[0].text == "MENU"
