from __future__ import annotations

import sqlite3
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


def install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    record_ok: bool = True,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    monkeypatch.setattr(demo, "Message", FakeMessage)
    monkeypatch.setattr(demo, "safe_answer_callback", noop_answer)

    ack_calls: list[tuple[Any, ...]] = []
    events: list[tuple[Any, ...]] = []

    def record_ack(*args: Any) -> bool:
        ack_calls.append(args)
        return record_ok

    monkeypatch.setattr(demo, "record_demo_ack", record_ack)
    monkeypatch.setattr(
        demo,
        "log_event",
        lambda *args, **_kwargs: events.append(args),
    )
    return ack_calls, events


@pytest.mark.asyncio
async def test_demo_ack_rejects_invalid_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch)
    await demo.demo_ack(FakeCallback("demo:ack:work:1", message=object()))

    malformed = FakeCallback("demo:ack:work")
    await demo.demo_ack(malformed)
    assert "Некорректная кнопка" in malformed.message.answers[0][0]

    bad_kind = FakeCallback("demo:ack:other:1")
    await demo.demo_ack(bad_kind)
    assert "Некорректный тип" in bad_kind.message.answers[0][0]

    bad_id = FakeCallback("demo:ack:work:not-int")
    await demo.demo_ack(bad_id)
    assert "Некорректный идентификатор" in bad_id.message.answers[0][0]


@pytest.mark.asyncio
async def test_demo_ack_missing_record_stops_before_outcome_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _calls, events = install(monkeypatch, record_ok=False)
    callback = FakeCallback("demo:ack:work:10")

    await demo.demo_ack(callback)

    assert "не нашёл запись демо" in callback.message.answers[-1][0]
    assert events == []


@pytest.mark.asyncio
async def test_demo_ack_records_listen_and_requests_post_outcome_without_sales_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ack_calls, events = install(monkeypatch)
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: None)

    def forbidden_sales_schedule(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("demo_ack must not schedule commercial jobs before POST outcome")

    monkeypatch.setattr(demo, "add_job", forbidden_sales_schedule)
    monkeypatch.setattr(demo, "cancel_jobs", forbidden_sales_schedule)

    callback = FakeCallback("demo:ack:work:10")
    await demo.demo_ack(callback)

    assert len(ack_calls) == 1
    assert ack_calls[0][0:3] == (7, "work", 10)
    assert isinstance(ack_calls[0][3], str)

    first_text = callback.message.answers[0][0]
    assert "Спасибо" in first_text
    assert "оцените своё состояние после практики" in first_text
    assert "reply_markup" not in callback.message.answers[0][1]

    event_names = [str(args[1]) for args in events]
    assert event_names == [
        "trial_outcome_requested_after_ack",
        "trial_conversion_waiting_for_outcome",
    ]


@pytest.mark.asyncio
async def test_demo_ack_optional_micro_question_after_outcome_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch)
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: "energy")
    monkeypatch.setattr(
        demo,
        "get_micro_question",
        lambda key: {
            "key": key,
            "question": "Как состояние?",
            "options": ["лучше", "так же"],
        },
    )
    monkeypatch.setattr(demo, "kb_micro_question", lambda key, options: (key, options))

    callback = FakeCallback("demo:ack:work:10")
    await demo.demo_ack(callback)

    assert len(callback.message.answers) == 2
    assert callback.message.answers[1][0] == "Как состояние?"
    assert callback.message.answers[1][1]["reply_markup"] == (
        "energy",
        ["лучше", "так же"],
    )


@pytest.mark.asyncio
async def test_demo_ack_empty_micro_question_does_not_add_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch)
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: "missing")
    monkeypatch.setattr(demo, "get_micro_question", lambda _key: None)

    callback = FakeCallback("demo:ack:home:10")
    await demo.demo_ack(callback)

    assert len(callback.message.answers) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "micro_error",
    [sqlite3.OperationalError("db"), ValueError("bad question")],
)
async def test_demo_ack_micro_question_fail_open_after_ack(
    monkeypatch: pytest.MonkeyPatch,
    micro_error: BaseException,
) -> None:
    install(monkeypatch)
    monkeypatch.setattr(
        demo,
        "should_offer_micro_question",
        lambda _uid: (_ for _ in ()).throw(micro_error),
    )

    callback = FakeCallback("demo:ack:home:10")
    await demo.demo_ack(callback)

    assert len(callback.message.answers) == 1
    assert "оцените своё состояние после практики" in callback.message.answers[0][0]
