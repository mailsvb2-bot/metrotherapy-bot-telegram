from __future__ import annotations

from typing import Any

import pytest

from handlers import demo


class FakeUser:
    def __init__(self, user_id: int = 7) -> None:
        self.id = user_id


class FakeMessage:
    def __init__(self, user_id: int = 7) -> None:
        self.from_user = FakeUser(user_id)
        self.answers: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


class FakeCallback:
    def __init__(
        self,
        data: str,
        *,
        user_id: int = 7,
        message: Any | None = None,
    ) -> None:
        self.data = data
        self.from_user = FakeUser(user_id)
        self.message = message if message is not None else FakeMessage(user_id)


async def noop_answer(_cb: Any) -> None:
    return None


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo, "Message", FakeMessage)
    monkeypatch.setattr(demo, "safe_answer_callback", noop_answer)


def test_callback_message_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch)
    message = FakeMessage()
    assert demo._callback_message(FakeCallback("x", message=message)) is message
    assert demo._callback_message(FakeCallback("x", message=object())) is None


@pytest.mark.asyncio
async def test_demo_send_other_validates_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch)
    await demo.demo_send_other(FakeCallback("demo:other:work", message=object()))

    malformed = FakeCallback("demo:other", message=FakeMessage())
    await demo.demo_send_other(malformed)
    assert malformed.message.answers == []

    invalid = FakeCallback("demo:other:bad", message=FakeMessage())
    await demo.demo_send_other(invalid)
    assert invalid.message.answers == []


@pytest.mark.asyncio
async def test_demo_send_other_blocks_free_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch)
    monkeypatch.setattr(demo, "can_repeat_demo_for_user", lambda _uid: False)
    monkeypatch.setattr(demo, "kb_sales_offer", lambda uid: ("sales", uid))

    monkeypatch.setattr(demo, "demo_sent_kinds", lambda _uid: {"work", "home"})
    both = FakeCallback("demo:other:work")
    await demo.demo_send_other(both)
    assert "оба ресурсных" in both.message.answers[0][0]
    assert both.message.answers[0][1]["reply_markup"] == ("sales", 7)

    monkeypatch.setattr(demo, "demo_sent_kinds", lambda _uid: {"work"})
    repeated = FakeCallback("demo:other:work")
    await demo.demo_send_other(repeated)
    assert "уже был отправлен" in repeated.message.answers[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("bypass", [False, True])
async def test_demo_send_other_schedules_only_cross_demo_delivery(
    monkeypatch: pytest.MonkeyPatch,
    bypass: bool,
) -> None:
    install(monkeypatch)
    monkeypatch.setattr(demo, "can_repeat_demo_for_user", lambda _uid: bypass)
    monkeypatch.setattr(demo, "demo_sent_kinds", lambda _uid: {"home"} if bypass else set())
    cancelled: list[tuple[Any, ...]] = []
    jobs: list[tuple[Any, ...]] = []
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(demo, "cancel_jobs", lambda *args, **kwargs: cancelled.append((args, kwargs)))
    monkeypatch.setattr(demo, "add_job", lambda *args, **kwargs: jobs.append((args, kwargs)))
    monkeypatch.setattr(demo, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))

    callback = FakeCallback("demo:other:work")
    await demo.demo_send_other(callback)

    assert cancelled == [((7,), {"job_types": ["demo_send"]})]
    assert len(jobs) == 1
    assert jobs[0][0][0:2] == (7, "demo_send")
    assert jobs[0][0][3] == {"kind": "work", "src": "cross"}
    assert events[0][0][1] == "demo_cross_requested"
    assert "второй ресурсный" in callback.message.answers[-1][0]
