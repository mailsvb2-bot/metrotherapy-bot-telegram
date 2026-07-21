from __future__ import annotations

import builtins
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator
from zoneinfo import ZoneInfoNotFoundError

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


def install_message_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo, "Message", FakeMessage)
    monkeypatch.setattr(demo, "safe_answer_callback", noop_answer)


def test_callback_message_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    install_message_type(monkeypatch)
    message = FakeMessage()
    assert demo._callback_message(FakeCallback("x", message=message)) is message
    assert demo._callback_message(FakeCallback("x", message=object())) is None


@pytest.mark.asyncio
async def test_demo_send_other_validates_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    install_message_type(monkeypatch)
    missing = FakeCallback("demo:other:work", message=object())
    await demo.demo_send_other(missing)

    malformed = FakeCallback("demo:other", message=FakeMessage())
    await demo.demo_send_other(malformed)
    assert malformed.message.answers == []

    invalid = FakeCallback("demo:other:bad", message=FakeMessage())
    await demo.demo_send_other(invalid)
    assert invalid.message.answers == []


@pytest.mark.asyncio
async def test_demo_send_other_blocks_free_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    install_message_type(monkeypatch)
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
async def test_demo_send_other_schedules_cross_demo(
    monkeypatch: pytest.MonkeyPatch,
    bypass: bool,
) -> None:
    install_message_type(monkeypatch)
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
    assert jobs[0][0][0:2] == (7, "demo_send")
    assert jobs[0][0][3] == {"kind": "work", "src": "cross"}
    assert events[0][0][1] == "demo_cross_requested"
    assert "второй ресурсный" in callback.message.answers[-1][0]


@contextmanager
def fake_db(row: Any) -> Iterator[Any]:
    class Conn:
        def execute(self, _sql: str, _params: tuple[Any, ...]) -> Any:
            return SimpleNamespace(fetchone=lambda: row)

    yield Conn()


def test_get_demo_sent_at_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo, "db", lambda: fake_db({"sent_at_utc": "2026-07-20T08:15:00+00:00"}))
    assert demo._get_demo_sent_at_utc(7, "work", 10) == "2026-07-20T08:15:00+00:00"
    monkeypatch.setattr(demo, "db", lambda: fake_db({"sent_at_utc": None}))
    assert demo._get_demo_sent_at_utc(7, "work", 10) is None
    monkeypatch.setattr(demo, "db", lambda: fake_db(None))
    assert demo._get_demo_sent_at_utc(7, "work", 10) is None


@pytest.mark.asyncio
async def test_demo_ack_rejects_invalid_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    install_message_type(monkeypatch)
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
async def test_demo_ack_missing_record(monkeypatch: pytest.MonkeyPatch) -> None:
    install_message_type(monkeypatch)
    monkeypatch.setattr(demo, "record_demo_ack", lambda *_args: False)
    callback = FakeCallback("demo:ack:work:10")
    await demo.demo_ack(callback)
    assert "не нашёл запись демо" in callback.message.answers[-1][0]


@pytest.mark.asyncio
async def test_demo_ack_active_subscription_and_micro_question(monkeypatch: pytest.MonkeyPatch) -> None:
    install_message_type(monkeypatch)
    monkeypatch.setattr(demo, "record_demo_ack", lambda *_args: True)
    monkeypatch.setattr(demo, "kb_sales_offer", lambda uid: ("sales", uid))
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: "energy")
    monkeypatch.setattr(
        demo,
        "get_micro_question",
        lambda key: {"key": key, "question": "Как состояние?", "options": ["лучше", "так же"]},
    )
    monkeypatch.setattr(demo, "kb_micro_question", lambda key, options: (key, options))
    monkeypatch.setattr(demo, "store", SimpleNamespace(is_sub_active=lambda _uid: True))

    callback = FakeCallback("demo:ack:work:10")
    await demo.demo_ack(callback)

    assert "Спасибо" in callback.message.answers[0][0]
    assert callback.message.answers[0][1]["reply_markup"] == ("sales", 7)
    assert callback.message.answers[1][0] == "Как состояние?"
    assert callback.message.answers[1][1]["reply_markup"] == ("energy", ["лучше", "так же"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "micro_error",
    [sqlite3.OperationalError("db"), ValueError("bad question")],
)
async def test_demo_ack_micro_question_fail_open(
    monkeypatch: pytest.MonkeyPatch,
    micro_error: BaseException,
) -> None:
    install_message_type(monkeypatch)
    monkeypatch.setattr(demo, "record_demo_ack", lambda *_args: True)
    monkeypatch.setattr(demo, "kb_sales_offer", lambda _uid: "sales")
    monkeypatch.setattr(
        demo,
        "should_offer_micro_question",
        lambda _uid: (_ for _ in ()).throw(micro_error),
    )
    monkeypatch.setattr(demo, "store", SimpleNamespace(is_sub_active=lambda _uid: True))
    callback = FakeCallback("demo:ack:home:10")
    await demo.demo_ack(callback)
    assert len(callback.message.answers) == 1


async def run_inactive_ack(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile: str = "standard",
    sent_at: str | None = "2026-07-20T08:15:00+00:00",
    add_job_impl: Any | None = None,
) -> tuple[FakeCallback, list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    install_message_type(monkeypatch)
    monkeypatch.setattr(demo, "record_demo_ack", lambda *_args: True)
    monkeypatch.setattr(demo, "kb_sales_offer", lambda _uid: "sales")
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: None)
    monkeypatch.setattr(demo, "store", SimpleNamespace(is_sub_active=lambda _uid: False))
    monkeypatch.setattr(
        demo,
        "settings",
        SimpleNamespace(
            FUNNEL_POSTDEMO_MINUTES=5,
            FUNNEL_DEADLINE_HOURS=24,
            FUNNEL_LASTCALL_HOURS=48,
            TIMEZONE="UTC",
        ),
    )
    monkeypatch.setattr(demo, "_get_demo_sent_at_utc", lambda *_args: sent_at)

    import services.ai as ai

    async def choose(_uid: int, *, kind: str) -> str:
        assert kind in {"work", "home"}
        return profile

    recorded_profiles: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ai, "choose_funnel_profile_async", choose)
    monkeypatch.setattr(ai, "record_funnel_profile", lambda *args, **kwargs: recorded_profiles.append((args, kwargs)))

    jobs: list[tuple[Any, ...]] = []
    if add_job_impl is None:
        monkeypatch.setattr(demo, "add_job", lambda *args: jobs.append(args))
    else:
        monkeypatch.setattr(demo, "add_job", add_job_impl)
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(demo, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))

    callback = FakeCallback("demo:ack:work:10")
    await demo.demo_ack(callback)
    return callback, jobs, recorded_profiles


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["standard", "urgent", "soft"])
async def test_demo_ack_schedules_profiled_funnel(
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    callback, jobs, profiles = await run_inactive_ack(monkeypatch, profile=profile)
    job_types = [job[1] for job in jobs]
    assert "funnel2_demo_nopay_24h" in job_types
    assert "funnel_postdemo" in job_types
    assert job_types.count("funnel_offer") == 2
    if profile in {"standard", "urgent"}:
        assert {"funnel_nudge", "funnel_deadline", "funnel_lastcall"}.issubset(job_types)
    else:
        assert "funnel_nudge" not in job_types
    assert profiles and profiles[0][0][1] == profile
    assert "Спасибо" in callback.message.answers[0][0]


@pytest.mark.asyncio
async def test_demo_ack_nextday_without_sent_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    _callback, jobs, _profiles = await run_inactive_ack(monkeypatch, sent_at=None)
    nextday = [job for job in jobs if job[1] == "funnel_offer" and job[3]["variant"] == "nextday_same_time"]
    assert len(nextday) == 1


@pytest.mark.asyncio
async def test_demo_ack_ai_import_and_runtime_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def import_hook(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "services.ai":
            raise ImportError("ai unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_hook)
    callback, jobs, _profiles = await run_inactive_ack(monkeypatch, profile="standard")
    assert callback.message.answers
    assert any(job[1] == "funnel_nudge" for job in jobs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ai_error",
    [sqlite3.OperationalError("db"), ValueError("profile")],
)
async def test_demo_ack_ai_errors_use_standard_profile(
    monkeypatch: pytest.MonkeyPatch,
    ai_error: BaseException,
) -> None:
    install_message_type(monkeypatch)
    monkeypatch.setattr(demo, "record_demo_ack", lambda *_args: True)
    monkeypatch.setattr(demo, "kb_sales_offer", lambda _uid: "sales")
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: None)
    monkeypatch.setattr(demo, "store", SimpleNamespace(is_sub_active=lambda _uid: False))
    monkeypatch.setattr(
        demo,
        "settings",
        SimpleNamespace(
            FUNNEL_POSTDEMO_MINUTES=5,
            FUNNEL_DEADLINE_HOURS=24,
            FUNNEL_LASTCALL_HOURS=48,
            TIMEZONE="UTC",
        ),
    )
    monkeypatch.setattr(demo, "_get_demo_sent_at_utc", lambda *_args: None)

    import services.ai as ai

    async def fail(*_args: Any, **_kwargs: Any) -> str:
        raise ai_error

    monkeypatch.setattr(ai, "choose_funnel_profile_async", fail)
    monkeypatch.setattr(ai, "record_funnel_profile", lambda *_args, **_kwargs: None)
    jobs: list[tuple[Any, ...]] = []
    monkeypatch.setattr(demo, "add_job", lambda *args: jobs.append(args))
    monkeypatch.setattr(demo, "log_event", lambda *_args, **_kwargs: None)

    await demo.demo_ack(FakeCallback("demo:ack:home:10"))
    assert any(job[1] == "funnel_nudge" for job in jobs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schedule_error",
    [sqlite3.OperationalError("db"), ValueError("bad schedule")],
)
async def test_demo_ack_optional_profile_jobs_fail_open(
    monkeypatch: pytest.MonkeyPatch,
    schedule_error: BaseException,
) -> None:
    jobs: list[tuple[Any, ...]] = []

    def add_job(*args: Any) -> None:
        jobs.append(args)
        if args[1] == "funnel_nudge":
            raise schedule_error

    install_message_type(monkeypatch)
    monkeypatch.setattr(demo, "record_demo_ack", lambda *_args: True)
    monkeypatch.setattr(demo, "kb_sales_offer", lambda _uid: "sales")
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: None)
    monkeypatch.setattr(demo, "store", SimpleNamespace(is_sub_active=lambda _uid: False))
    monkeypatch.setattr(
        demo,
        "settings",
        SimpleNamespace(
            FUNNEL_POSTDEMO_MINUTES=5,
            FUNNEL_DEADLINE_HOURS=24,
            FUNNEL_LASTCALL_HOURS=48,
            TIMEZONE="UTC",
        ),
    )
    monkeypatch.setattr(demo, "_get_demo_sent_at_utc", lambda *_args: None)
    import services.ai as ai

    async def choose(*_args: Any, **_kwargs: Any) -> str:
        return "standard"

    monkeypatch.setattr(ai, "choose_funnel_profile_async", choose)
    monkeypatch.setattr(ai, "record_funnel_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(demo, "add_job", add_job)
    monkeypatch.setattr(demo, "log_event", lambda *_args, **_kwargs: None)

    await demo.demo_ack(FakeCallback("demo:ack:work:10"))
    assert any(job[1] == "funnel_offer" for job in jobs)


@pytest.mark.asyncio
async def test_demo_ack_bad_timezone_uses_nextday_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    install_message_type(monkeypatch)
    monkeypatch.setattr(demo, "record_demo_ack", lambda *_args: True)
    monkeypatch.setattr(demo, "kb_sales_offer", lambda _uid: "sales")
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: None)
    monkeypatch.setattr(demo, "store", SimpleNamespace(is_sub_active=lambda _uid: False))
    monkeypatch.setattr(
        demo,
        "settings",
        SimpleNamespace(
            FUNNEL_POSTDEMO_MINUTES=5,
            FUNNEL_DEADLINE_HOURS=24,
            FUNNEL_LASTCALL_HOURS=48,
            TIMEZONE="Missing/Zone",
        ),
    )
    monkeypatch.setattr(demo, "_get_demo_sent_at_utc", lambda *_args: "bad timestamp")
    import services.ai as ai

    async def choose(*_args: Any, **_kwargs: Any) -> str:
        return "soft"

    monkeypatch.setattr(ai, "choose_funnel_profile_async", choose)
    monkeypatch.setattr(ai, "record_funnel_profile", lambda *_args, **_kwargs: None)
    jobs: list[tuple[Any, ...]] = []
    monkeypatch.setattr(demo, "add_job", lambda *args: jobs.append(args))
    monkeypatch.setattr(demo, "log_event", lambda *_args, **_kwargs: None)

    def missing_zone(_name: str) -> Any:
        raise ZoneInfoNotFoundError("missing")

    monkeypatch.setattr(demo, "ZoneInfo", missing_zone)
    await demo.demo_ack(FakeCallback("demo:ack:home:10"))
    assert any(job[1] == "funnel_offer" and job[3]["variant"] == "nextday_fallback" for job in jobs)
