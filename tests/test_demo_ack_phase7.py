from __future__ import annotations

import builtins
import sqlite3
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

import pytest
import services.ai as ai_service

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


def configure_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subscribed: bool,
    record_ok: bool = True,
) -> None:
    install(monkeypatch)
    monkeypatch.setattr(demo, "record_demo_ack", lambda *_args: record_ok)
    monkeypatch.setattr(demo, "kb_sales_offer", lambda uid: ("sales", uid))
    monkeypatch.setattr(demo, "store", SimpleNamespace(is_sub_active=lambda _uid: subscribed))


@pytest.mark.asyncio
async def test_demo_ack_rejects_invalid_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_common(monkeypatch, subscribed=True)
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
    configure_common(monkeypatch, subscribed=True, record_ok=False)
    callback = FakeCallback("demo:ack:work:10")
    await demo.demo_ack(callback)
    assert "не нашёл запись демо" in callback.message.answers[-1][0]


@pytest.mark.asyncio
async def test_demo_ack_active_subscription_and_micro_question(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_common(monkeypatch, subscribed=True)
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: "energy")
    monkeypatch.setattr(
        demo,
        "get_micro_question",
        lambda key: {"key": key, "question": "Как состояние?", "options": ["лучше", "так же"]},
    )
    monkeypatch.setattr(demo, "kb_micro_question", lambda key, options: (key, options))

    callback = FakeCallback("demo:ack:work:10")
    await demo.demo_ack(callback)

    assert "Спасибо" in callback.message.answers[0][0]
    assert callback.message.answers[0][1]["reply_markup"] == ("sales", 7)
    assert callback.message.answers[1][0] == "Как состояние?"
    assert callback.message.answers[1][1]["reply_markup"] == ("energy", ["лучше", "так же"])


@pytest.mark.asyncio
async def test_demo_ack_empty_micro_question(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_common(monkeypatch, subscribed=True)
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
async def test_demo_ack_micro_question_fail_open(
    monkeypatch: pytest.MonkeyPatch,
    micro_error: BaseException,
) -> None:
    configure_common(monkeypatch, subscribed=True)
    monkeypatch.setattr(
        demo,
        "should_offer_micro_question",
        lambda _uid: (_ for _ in ()).throw(micro_error),
    )
    callback = FakeCallback("demo:ack:home:10")
    await demo.demo_ack(callback)
    assert len(callback.message.answers) == 1


def configure_inactive(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile: str = "standard",
    sent_at: str | None = "2026-07-20T08:15:00+00:00",
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    configure_common(monkeypatch, subscribed=False)
    monkeypatch.setattr(demo, "should_offer_micro_question", lambda _uid: None)
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

    async def choose(_uid: int, *, kind: str) -> str:
        assert kind in {"work", "home"}
        return profile

    profiles: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ai_service, "choose_funnel_profile_async", choose)
    monkeypatch.setattr(
        ai_service,
        "record_funnel_profile",
        lambda *args, **kwargs: profiles.append((args, kwargs)),
    )
    jobs: list[tuple[Any, ...]] = []
    monkeypatch.setattr(demo, "add_job", lambda *args: jobs.append(args))
    monkeypatch.setattr(demo, "log_event", lambda *_args, **_kwargs: None)
    return jobs, profiles


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["standard", "urgent", "soft"])
async def test_demo_ack_schedules_profiled_funnel(
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    jobs, profiles = configure_inactive(monkeypatch, profile=profile)
    callback = FakeCallback("demo:ack:work:10")
    await demo.demo_ack(callback)

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
    jobs, _profiles = configure_inactive(monkeypatch, sent_at=None)
    await demo.demo_ack(FakeCallback("demo:ack:work:10"))
    nextday = [job for job in jobs if job[1] == "funnel_offer" and job[3]["variant"] == "nextday_same_time"]
    assert len(nextday) == 1


@pytest.mark.asyncio
async def test_demo_ack_ai_import_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs, _profiles = configure_inactive(monkeypatch)
    original_import = builtins.__import__

    def import_hook(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "services.ai":
            raise ImportError("ai unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_hook)
    await demo.demo_ack(FakeCallback("demo:ack:work:10"))
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
    jobs, _profiles = configure_inactive(monkeypatch)

    async def fail(*_args: Any, **_kwargs: Any) -> str:
        raise ai_error

    monkeypatch.setattr(ai_service, "choose_funnel_profile_async", fail)
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
    configure_inactive(monkeypatch)
    jobs: list[tuple[Any, ...]] = []

    def add_job(*args: Any) -> None:
        jobs.append(args)
        if args[1] == "funnel_nudge":
            raise schedule_error

    monkeypatch.setattr(demo, "add_job", add_job)
    await demo.demo_ack(FakeCallback("demo:ack:work:10"))
    assert any(job[1] == "funnel_offer" for job in jobs)


@pytest.mark.asyncio
async def test_demo_ack_bad_timezone_uses_nextday_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs, _profiles = configure_inactive(monkeypatch, profile="soft", sent_at="bad timestamp")
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

    def missing_zone(_name: str) -> Any:
        raise ZoneInfoNotFoundError("missing")

    monkeypatch.setattr(demo, "ZoneInfo", missing_zone)
    await demo.demo_ack(FakeCallback("demo:ack:home:10"))
    assert any(job[1] == "funnel_offer" and job[3]["variant"] == "nextday_fallback" for job in jobs)
