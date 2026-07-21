from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from services.messenger import text_ui as ui


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


def test_reminder_tuple_row_and_default_time(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def fake_db() -> Iterator[Conn]:
        yield Conn(("07:15",))

    jobs: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ui, "db", fake_db)
    monkeypatch.setattr(ui.settings, "TIMEZONE", "Europe/Amsterdam")
    monkeypatch.setattr(ui.settings, "MORNING_TIME", "bad")
    monkeypatch.setattr(ui, "cancel_jobs", lambda *args, **kwargs: jobs.append(("cancel", args, kwargs)))
    monkeypatch.setattr(ui, "add_job", lambda *args, **kwargs: jobs.append(("add", args, kwargs)))
    text = ui._schedule_continue_tomorrow_text(3)
    assert "08:30" in text
    add = next(call for call in jobs if call[0] == "add")
    assert add[1][3]["hhmm"] == "08:30"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("menu:main", ("menu", None)),
        ("settings:menu", ("settings", None)),
        ("settings:state", ("progress", None)),
        ("share:menu", ("share", None)),
        ("settings:platform:menu", ("settings", None)),
        ("settings:delivery:slot:morning", ("settings_delivery_slot", "morning")),
        ("demo", ("demo", None)),
        ("weather", ("weather", None)),
    ],
)
def test_parser_callback_edges(monkeypatch: pytest.MonkeyPatch, text: str, expected: tuple[str, str | None]) -> None:
    monkeypatch.setattr(ui, "normalize_menu_command", lambda _raw: None)
    assert ui._parse_command(text) == expected


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "normalize_platform", lambda value: str(value).lower())
    monkeypatch.setattr(ui, "parse_score_text", lambda _text: None)
    monkeypatch.setattr(
        ui,
        "register_user_entry",
        lambda *_args, **_kwargs: SimpleNamespace(user_id=91, linked_via_bridge=False),
    )
    monkeypatch.setattr(ui, "peek_pending", lambda _uid: None)
    monkeypatch.setattr(ui, "pop_pending", lambda _uid: None)
    monkeypatch.setattr(ui, "get_preface", lambda *_args, **_kwargs: "")


def incoming(text: str, *, platform: str = "vk") -> list[ui.MessengerReply]:
    return ui.handle_incoming_text(
        5,
        platform=platform,
        external_user_id="external",
        text=text,
    )[1]


def test_legacy_telegram_done_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch)
    calls: list[tuple[Any, ...]] = []

    def confirm(user_id: int, *args: Any, **kwargs: Any):
        calls.append((user_id, args, kwargs))
        if kwargs:
            raise TypeError("legacy signature")
        return SimpleNamespace(anchor=4, title="Legacy")

    monkeypatch.setattr(ui, "confirm_pending_audio_delivery", confirm)
    replies = incoming("done", platform="telegram")
    assert "№4" in replies[0].text
    assert len(calls) == 2
    assert calls[1][2] == {}


def test_payment_and_gift_alias_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch)
    monkeypatch.setattr(ui, "_payment_text", lambda *_args, **_kwargs: "PAY")
    monkeypatch.setattr(ui, "_start_gift_recipient_flow", lambda _uid: ui.MessengerReply(text="GIFT"))
    assert incoming("payment")[0].text == "PAY"
    assert incoming("sub:menu")[0].text == "PAY"
    assert incoming("gift:menu")[0].text == "GIFT"


def test_linked_bridge_without_resume_and_final_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch)
    monkeypatch.setattr(
        ui,
        "register_user_entry",
        lambda *_args, **_kwargs: SimpleNamespace(user_id=91, linked_via_bridge=True),
    )
    monkeypatch.setattr(ui, "_bridge_linked_text", lambda *_args: "LINKED")
    monkeypatch.setattr(ui, "_should_auto_resume_after_bridge", lambda _uid: False)
    monkeypatch.setattr(ui, "_menu_text", lambda _uid: "MENU")
    replies = incoming("start")
    assert [reply.text for reply in replies] == ["LINKED", "MENU"]

    monkeypatch.setattr(ui, "_parse_command", lambda _text: ("unhandled_action", None))
    monkeypatch.setattr(
        ui,
        "register_user_entry",
        lambda *_args, **_kwargs: SimpleNamespace(user_id=91, linked_via_bridge=False),
    )
    assert incoming("anything")[0].text == "MENU"


def test_plain_score_uses_post_and_no_stage_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch)
    monkeypatch.setattr(ui, "parse_score_text", lambda _text: 6)
    monkeypatch.setattr(ui, "_pending_score_stage", lambda _uid: "post")
    assert incoming("6")[0].kind == "auto_post_score"

    monkeypatch.setattr(ui, "_pending_score_stage", lambda _uid: None)
    monkeypatch.setattr(ui, "_menu_text", lambda _uid: "MENU")
    assert incoming("6")[0].text == "MENU"
