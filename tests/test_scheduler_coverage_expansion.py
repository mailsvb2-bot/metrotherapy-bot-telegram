from __future__ import annotations

import asyncio
import sqlite3
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from core import engine as engine_module
from core.ai import reward_engine
from core.runtime import self_healing
from services import auto_audio, scheduler
from services.ai import ux_guard
from services.payments import retry_queue


@pytest.mark.asyncio
async def test_coro_names_and_task_creation_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sample() -> None:
        return None

    coro = sample()
    assert scheduler._coro_name(sample) == "sample"
    assert scheduler._coro_name(coro) == "sample"
    assert scheduler._coro_name(object()) == "object"
    coro.close()

    loop = asyncio.get_running_loop()

    class Manager:
        def create(self, task_coro: Any) -> asyncio.Task:
            return loop.create_task(task_coro)

    import services.bg

    monkeypatch.setattr(services.bg, "tm", lambda: Manager())
    task = scheduler._tm_create(sample())
    await task
    assert task.done()

    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        scheduler,
        "_record_scheduler_error",
        lambda source, exc: errors.append((source, type(exc).__name__)),
    )

    async def fail() -> None:
        raise RuntimeError("secret")

    task = scheduler._tm_create(fail())
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert errors == [("fail", "RuntimeError")]

    def broken_tm() -> Any:
        raise RuntimeError("owner unavailable")

    monkeypatch.setattr(services.bg, "tm", broken_tm)
    fallback = scheduler._tm_create(sample())
    await fallback
    assert fallback.done()


@pytest.mark.asyncio
async def test_precise_scheduler_start_schedule_run_and_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(scheduler, "_tm_create", lambda coro: loop.create_task(coro))
    precise = scheduler.PreciseScheduler()
    await precise.start()
    first_task = precise._task
    await precise.start()
    assert precise._task is first_task

    fired: list[str] = []
    event = asyncio.Event()

    async def factory() -> None:
        fired.append("now")
        event.set()

    await precise.schedule_at(loop.time() + 0.01, factory)
    await asyncio.wait_for(event.wait(), timeout=1)
    assert fired == ["now"]
    await precise.stop()
    assert precise._task is None
    assert precise._running is False
    await precise.stop()


@pytest.mark.asyncio
async def test_protected_tick_error_recording_and_owner_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        scheduler,
        "_record_scheduler_error",
        lambda source, exc: recorded.append((source, type(exc).__name__)),
    )

    async def ok() -> object:
        return object()

    async def fail() -> object:
        raise ValueError("secret")

    assert await scheduler._run_protected_tick("ok", ok) is True
    assert await scheduler._run_protected_tick("fail", fail) is False
    assert recorded == [("fail", "ValueError")]

    async def cancel() -> object:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await scheduler._run_protected_tick("cancel", cancel)

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(scheduler, "_tm_create", lambda coro: loop.create_task(coro))
    monkeypatch.setattr(scheduler, "env_float", lambda *_args, **_kwargs: 0.01)
    scheduler._owner_tasks.clear()
    scheduler._owner_started_at.clear()

    gate = asyncio.Event()

    async def slow() -> object:
        await gate.wait()
        return object()

    task = scheduler._start_owner_tick("owner", slow)
    assert task is not None
    assert scheduler._start_owner_tick("owner", slow) is None
    await asyncio.sleep(0.03)
    assert task.done()
    await asyncio.sleep(0)
    assert "owner" not in scheduler._owner_tasks
    assert ("owner", "TimeoutError") in recorded

    async def quick() -> object:
        return object()

    task = scheduler._start_owner_tick("quick", quick)
    assert task is not None
    await task
    await asyncio.sleep(0)
    assert "quick" not in scheduler._owner_tasks


def test_scheduler_singleton_health_and_error_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler._precise = None
    first = scheduler.get_precise_scheduler()
    assert scheduler.get_precise_scheduler() is first

    old = (
        scheduler._bg_task,
        scheduler._bg_started_at_monotonic,
        scheduler._bg_iteration_count,
        scheduler._bg_error_count,
        scheduler._bg_last_error,
        scheduler._bg_last_error_at_monotonic,
        scheduler._bg_last_tick_at_monotonic,
        dict(scheduler._owner_tasks),
        dict(scheduler._owner_started_at),
    )
    try:
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: 100.0)
        scheduler._record_scheduler_error("source", RuntimeError("payload"))
        assert scheduler._bg_last_error == "source:RuntimeError"

        class Task:
            def __init__(self, done: bool = False) -> None:
                self._done = done

            def done(self) -> bool:
                return self._done

        scheduler._bg_task = Task(False)  # type: ignore[assignment]
        scheduler._bg_started_at_monotonic = 10.0
        scheduler._bg_iteration_count = 7
        scheduler._bg_last_error_at_monotonic = 80.0
        scheduler._bg_last_tick_at_monotonic = 90.0
        first._running = True
        first._task = Task(False)  # type: ignore[assignment]
        first._queue = [(1.0, 1, lambda: None)]  # type: ignore[list-item]
        scheduler._owner_tasks.clear()
        scheduler._owner_tasks["owner"] = Task(False)  # type: ignore[assignment]
        scheduler._owner_started_at["owner"] = 70.0
        monkeypatch.setattr(
            retry_queue,
            "payment_retry_health_snapshot",
            lambda: {"payment_retry_active": 2, "payment_retry_dead": 1},
        )
        snapshot = scheduler.scheduler_health_snapshot()
        assert snapshot["scheduler_loop_task_running"] is True
        assert snapshot["precise_scheduler_running"] is True
        assert snapshot["precise_scheduler_queue_size"] == 1
        assert snapshot["scheduler_loop_uptime_sec"] == 90
        assert snapshot["scheduler_owner_tasks_running"] == 1
        assert snapshot["scheduler_owner_oldest_age_sec"] == 30
        assert snapshot["payment_retry_active"] == 2
    finally:
        (
            scheduler._bg_task,
            scheduler._bg_started_at_monotonic,
            scheduler._bg_iteration_count,
            scheduler._bg_error_count,
            scheduler._bg_last_error,
            scheduler._bg_last_error_at_monotonic,
            scheduler._bg_last_tick_at_monotonic,
            owner_tasks,
            owner_started,
        ) = old
        scheduler._owner_tasks.clear()
        scheduler._owner_tasks.update(owner_tasks)
        scheduler._owner_started_at.clear()
        scheduler._owner_started_at.update(owner_started)
        first._running = False
        first._task = None
        first._queue = []


@pytest.mark.asyncio
async def test_ux_guard_real_and_safe_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Conn:
        def execute(self, query: str) -> None:
            calls.append(query)

    monkeypatch.setattr(scheduler.os, "environ", {"APP_ENV": "prod"})
    import services.db

    monkeypatch.setattr(services.db, "get_db", lambda: nullcontext(Conn()))
    monkeypatch.setattr(ux_guard, "analyze", lambda conn: calls.append(type(conn).__name__))
    await scheduler._run_ux_guard_tick()
    assert "PRAGMA query_only=ON" in calls
    assert "Conn" in calls

    async def raise_timeout() -> None:
        raise asyncio.TimeoutError

    monkeypatch.setattr(scheduler, "_run_ux_guard_tick", raise_timeout)
    await scheduler._safe_ux_guard_tick()

    async def missing_table() -> None:
        raise sqlite3.OperationalError("no such table: x")

    monkeypatch.setattr(scheduler, "_run_ux_guard_tick", missing_table)
    await scheduler._safe_ux_guard_tick()

    async def other_db() -> None:
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(scheduler, "_run_ux_guard_tick", other_db)
    await scheduler._safe_ux_guard_tick()

    async def expected() -> None:
        raise RuntimeError("expected")

    monkeypatch.setattr(scheduler, "_run_ux_guard_tick", expected)
    await scheduler._safe_ux_guard_tick()

    async def unexpected() -> None:
        raise BaseException("unexpected")

    monkeypatch.setattr(scheduler, "_run_ux_guard_tick", unexpected)
    with pytest.raises(BaseException):
        await scheduler._safe_ux_guard_tick()


@pytest.mark.asyncio
async def test_growth_bridge_and_payment_retry_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.growth_conversion_event_bridge as bridge

    bridge_calls: list[int] = []
    monkeypatch.setattr(scheduler, "env_int", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr(
        bridge,
        "run_event_conversion_bridge_safe",
        lambda *, batch_size: bridge_calls.append(batch_size) or SimpleNamespace(error="degraded"),
    )
    await scheduler._run_growth_conversion_bridge_tick()
    assert bridge_calls == [5]

    monkeypatch.setattr(
        retry_queue,
        "run_payment_retry_batch",
        lambda: SimpleNamespace(claimed=2, completed=1, rescheduled=0, dead=1),
    )
    await scheduler._run_payment_reconciliation_retry_tick()
    monkeypatch.setattr(
        retry_queue,
        "run_payment_retry_batch",
        lambda: SimpleNamespace(claimed=2, completed=2, rescheduled=0, dead=0),
    )
    await scheduler._run_payment_reconciliation_retry_tick()
    monkeypatch.setattr(
        retry_queue,
        "run_payment_retry_batch",
        lambda: SimpleNamespace(claimed=0, completed=0, rescheduled=0, dead=0),
    )
    await scheduler._run_payment_reconciliation_retry_tick()


@pytest.mark.asyncio
async def test_background_loop_schedules_all_owners(monkeypatch: pytest.MonkeyPatch) -> None:
    names: list[str] = []
    scheduler._bg_iteration_count = 0
    scheduler._bg_last_tick_at_monotonic = 0.0

    class Healing:
        def tick(self) -> None:
            return None

    monkeypatch.setattr(self_healing, "SelfHealingEngine", Healing)
    monkeypatch.setattr(reward_engine, "compute_and_store_rewards", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto_audio, "tick", lambda _bot: asyncio.sleep(0))
    monkeypatch.setattr(engine_module.engine, "tick", lambda _bot: asyncio.sleep(0))
    monkeypatch.setattr(scheduler, "env_float", lambda *_args, **_kwargs: 1.0)
    monkeypatch.setattr(scheduler, "env_int", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        scheduler,
        "_start_owner_tick",
        lambda name, factory: names.append(name) or None,
    )
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: 100.0)

    async def one_iteration(_seconds: float) -> None:
        if scheduler._bg_iteration_count:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", one_iteration)
    with pytest.raises(asyncio.CancelledError):
        await scheduler._background_loop(SimpleNamespace())
    assert set(names) == {
        "SelfHealingEngine.tick", "auto_audio.tick", "engine.tick", "RewardEngine.tick",
        "GrowthConversionBridge.tick", "PaymentReconciliationRetry.tick", "UXGuard.tick",
    }


@pytest.mark.asyncio
async def test_start_and_stop_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    scheduler._bg_task = None
    scheduler._bg_started_at_monotonic = 0.0
    scheduler._owner_tasks.clear()
    scheduler._owner_started_at.clear()

    class Precise:
        def __init__(self) -> None:
            self.started = 0
            self.stopped = 0

        async def start(self) -> None:
            self.started += 1

        async def stop(self) -> None:
            self.stopped += 1

    precise = Precise()
    monkeypatch.setattr(scheduler, "get_precise_scheduler", lambda: precise)

    async def background(_bot: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(scheduler, "_background_loop", background)
    monkeypatch.setattr(scheduler, "_tm_create", lambda coro: loop.create_task(coro))
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: 12.0)
    scheduler.start_scheduler(SimpleNamespace())
    first = scheduler._bg_task
    scheduler.start_scheduler(SimpleNamespace())
    assert scheduler._bg_task is first
    await asyncio.sleep(0)
    assert precise.started == 1

    owner = loop.create_task(asyncio.Event().wait(), name="owner")
    scheduler._owner_tasks["owner"] = owner
    scheduler._owner_started_at["owner"] = 1.0
    await scheduler.stop_scheduler()
    assert scheduler._bg_task is None
    assert scheduler._bg_started_at_monotonic == 0.0
    assert scheduler._owner_tasks == {}
    assert precise.stopped == 1
