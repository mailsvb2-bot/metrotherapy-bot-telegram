from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from handlers import gift_flow


class FakeMessage:
    def __init__(self, user_id: int | None = 7, *, edit_exc: BaseException | None = None) -> None:
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
        self.answers: list[tuple[str, dict[str, Any]]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []
        self.edit_exc = edit_exc

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append((text, kwargs))
        if self.edit_exc:
            raise self.edit_exc


class FakeCallback:
    def __init__(self, data: str | None, message: Any, user_id: int = 7) -> None:
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[tuple[tuple[Any, ...], dict[str, Any]]] = []


async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


async def safe_callback(cb: FakeCallback, *args: Any, **kwargs: Any) -> None:
    cb.answers.append((args, kwargs))


def patch_message_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gift_flow, "Message", FakeMessage)


def test_gift_callback_helpers_and_keyboards(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_message_type(monkeypatch)
    message = FakeMessage()
    callback = FakeCallback("gift:how:abc", message, user_id=9)
    assert gift_flow._callback_message(callback) is message
    assert gift_flow._callback_message(FakeCallback("x", object())) is None
    assert gift_flow._callback_user_id(callback) == 9
    assert gift_flow._message_user_id(message) == 7
    assert gift_flow._message_user_id(FakeMessage(None)) is None

    assert gift_flow._gift_code(callback, "gift:how:") == "abc"
    assert gift_flow._gift_code(FakeCallback(None, message), "gift:how:") is None
    assert gift_flow._gift_code(FakeCallback("gift:accept:abc", message), "gift:how:") is None

    intro = gift_flow._kb_intro("abc")
    assert [row[0].callback_data for row in intro.inline_keyboard] == [
        "gift:how:abc",
        "gift:accept:abc",
        "gift:time:abc",
    ]
    to_time = gift_flow._kb_to_time("abc")
    assert to_time.inline_keyboard[0][0].callback_data == "gift:time:abc"
    assert to_time.inline_keyboard[1][0].callback_data == "menu:main"


def test_gift_intro_state_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(gift_flow, "log_event", lambda *args: events.append(args))
    monkeypatch.setattr(gift_flow, "get_gift_status", lambda _code: (True, "ok", {"scope": "both"}))
    assert gift_flow._gift_intro_state("abc", 7) == (True, "ok")
    assert events[-1][1] == "gift_intro_shown"

    monkeypatch.setattr(gift_flow, "get_gift_status", lambda _code: (False, "invalid", None))
    assert gift_flow._gift_intro_state("abc", 7) == (False, "invalid")
    assert len(events) == 1


def test_accept_gift_state_all_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gift_flow, "get_gift_status", lambda _code: (False, "missing", None))
    assert gift_flow._accept_gift_state("abc", 7) == (False, "missing", False)

    monkeypatch.setattr(gift_flow, "get_gift_status", lambda _code: (True, "known", {"scope": "both"}))
    monkeypatch.setattr(gift_flow, "redeem_gift", lambda *_args: (False, "used", None))
    assert gift_flow._accept_gift_state("abc", 7) == (False, "used", True)

    grants: list[tuple[Any, ...]] = []
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        gift_flow,
        "redeem_gift",
        lambda *_args: (True, "accepted", {"scope": "both", "days": 30}),
    )
    monkeypatch.setattr(gift_flow, "grant", lambda *args: grants.append(args))
    monkeypatch.setattr(gift_flow, "log_event", lambda *args: events.append(args))
    assert gift_flow._accept_gift_state("abc", 7) == (True, "accepted", True)
    assert grants == [(7, "both", 30)]
    assert events[-1][1] == "gift_accepted"


def test_accept_gift_for_time_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    grants: list[tuple[Any, ...]] = []
    activations: list[tuple[Any, ...]] = []
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(gift_flow, "grant", lambda *args: grants.append(args))
    monkeypatch.setattr(gift_flow, "activate_gift", lambda *args: activations.append(args))
    monkeypatch.setattr(gift_flow, "log_event", lambda *args: events.append(args))

    monkeypatch.setattr(gift_flow, "get_gift_status", lambda _code: (False, "missing", None))
    gift_flow._accept_gift_for_time_state("abc", 7)
    assert grants == []

    monkeypatch.setattr(gift_flow, "get_gift_status", lambda _code: (True, "known", {"scope": "both"}))
    monkeypatch.setattr(gift_flow, "redeem_gift", lambda *_args: (False, "used", None))
    gift_flow._accept_gift_for_time_state("abc", 7)
    assert grants == []

    monkeypatch.setattr(
        gift_flow,
        "redeem_gift",
        lambda *_args: (True, "ok", {"scope": "morning", "days": 7}),
    )
    gift_flow._accept_gift_for_time_state("abc", 7)
    assert grants == [(7, "morning", 7)]
    assert activations == [("abc", 7)]
    assert events[-1][1] == "gift_redeemed"


@pytest.mark.asyncio
async def test_send_gift_intro_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gift_flow.asyncio, "to_thread", direct_to_thread)
    missing = FakeMessage(None)
    await gift_flow.send_gift_intro(missing, "abc")
    assert missing.answers == []

    monkeypatch.setattr(gift_flow, "_gift_intro_state", lambda *_args: (False, "invalid"))
    invalid = FakeMessage(7)
    await gift_flow.send_gift_intro(invalid, "abc")
    assert invalid.answers == [("invalid", {})]

    monkeypatch.setattr(gift_flow, "_gift_intro_state", lambda *_args: (True, "ok"))
    valid = FakeMessage(7)
    await gift_flow.send_gift_intro(valid, "abc")
    text, kwargs = valid.answers[0]
    assert text == gift_flow.GIFT_INTRO
    assert kwargs["parse_mode"] == "Markdown"
    assert kwargs["reply_markup"].inline_keyboard[1][0].callback_data == "gift:accept:abc"


@pytest.mark.asyncio
async def test_gift_how_and_later_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_message_type(monkeypatch)
    monkeypatch.setattr(gift_flow, "safe_answer_callback", safe_callback)

    invalid = FakeCallback("bad", FakeMessage())
    await gift_flow.gift_how(invalid)
    assert invalid.answers
    assert invalid.message.answers == []

    message = FakeMessage()
    await gift_flow.gift_how(FakeCallback("gift:how:abc", message))
    assert message.answers[0][0] == gift_flow.GIFT_EXPLAIN
    assert message.answers[0][1]["parse_mode"] == "Markdown"

    later_invalid = FakeCallback(None, FakeMessage())
    await gift_flow.gift_later(later_invalid)
    assert later_invalid.message.answers == []

    later = FakeMessage()
    await gift_flow.gift_later(FakeCallback("gift:later:abc", later))
    assert "Когда будете готовы" in later.answers[0][0]
    assert later.answers[0][1]["reply_markup"] is None


@pytest.mark.asyncio
async def test_gift_accept_success_fallback_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_message_type(monkeypatch)
    monkeypatch.setattr(gift_flow, "safe_answer_callback", safe_callback)
    monkeypatch.setattr(gift_flow.asyncio, "to_thread", direct_to_thread)

    invalid_callback = FakeCallback("bad", FakeMessage())
    await gift_flow.gift_accept(invalid_callback)
    assert invalid_callback.message.answers == []

    monkeypatch.setattr(gift_flow, "_accept_gift_state", lambda *_args: (True, "ok", True))
    edited = FakeMessage()
    await gift_flow.gift_accept(FakeCallback("gift:accept:abc", edited))
    assert edited.edits[0][0] == gift_flow.GIFT_EXPLAIN
    assert edited.answers == []

    class BadRequest(Exception):
        pass

    monkeypatch.setattr(gift_flow, "TelegramBadRequest", BadRequest)
    fallback = FakeMessage(edit_exc=BadRequest("not modified"))
    await gift_flow.gift_accept(FakeCallback("gift:accept:abc", fallback))
    assert fallback.answers[0][0] == gift_flow.GIFT_EXPLAIN

    monkeypatch.setattr(gift_flow, "_accept_gift_state", lambda *_args: (False, "already used", True))
    failed = FakeMessage()
    await gift_flow.gift_accept(FakeCallback("gift:accept:abc", failed))
    assert failed.answers == [("already used", {})]


@pytest.mark.asyncio
async def test_gift_time_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_message_type(monkeypatch)
    monkeypatch.setattr(gift_flow, "safe_answer_callback", safe_callback)
    monkeypatch.setattr(gift_flow.asyncio, "to_thread", direct_to_thread)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(gift_flow, "_accept_gift_for_time_state", lambda *args: calls.append(args))
    monkeypatch.setattr(gift_flow, "kb_after_paid", lambda: "after-paid")

    invalid = FakeCallback("bad", FakeMessage())
    await gift_flow.gift_time(invalid)
    assert calls == []

    message = FakeMessage()
    await gift_flow.gift_time(FakeCallback("gift:time:abc", message, user_id=9))
    assert calls == [("abc", 9)]
    assert "Подарок активирован" in message.answers[0][0]
    assert message.answers[0][1]["reply_markup"] == "after-paid"
