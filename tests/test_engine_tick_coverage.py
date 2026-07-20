from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from core import engine as engine_module


class Bot:
    async def send_message(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class Claimed:
    def __init__(
        self,
        *,
        job_type: str = "custom",
        payload: Any = "{}",
        retries: int = 0,
        job_key: str = "key",
    ) -> None:
        self.id = 1
        self.user_id = 2
        self.job_type = job_type
        self.run_at_utc = "2026-07-20T12:00:00+00:00"
        self.payload = payload
        self.job_key = job_key
        self.lock_token = "lock"
        self.retries = retries


def install_tick_base(monkeypatch: pytest.MonkeyPatch, engine: Any) -> dict[str, list[Any]]:
    engine._tick_lock = asyncio.Lock()
    engine._last_tick_monotonic = 0.0
    monkeypatch.setenv("ENGINE_TICK_MIN_INTERVAL", "0")
    counter = iter(range(10, 1000))
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: float(next(counter)))

    async def acquire(*_args: Any, **_kwargs: Any) -> bool:
        return True

    records: dict[str, list[Any]] = {
        "release": [], "done": [], "retry": [], "event": [], "delivery": [], "lock": []
    }

    async def release(name: str) -> None:
        records["release"].append(name)

    monkeypatch.setattr(engine_module, "acquire_lock", acquire)
    monkeypatch.setattr(engine_module, "release_lock", release)
    monkeypatch.setattr(engine_module, "utc_now_iso", lambda: "now")
    monkeypatch.setattr(
        engine_module,
        "mark_done",
        lambda *args, **kwargs: records["done"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        engine_module,
        "reschedule",
        lambda *args, **kwargs: records["retry"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        engine_module,
        "log_event",
        lambda *args: records["event"].append(args),
    )
    monkeypatch.setattr(
        engine_module,
        "mark_delivery_once",
        lambda *args: records["delivery"].append(args) or True,
    )
    monkeypatch.setattr(
        engine_module,
        "lock_job",
        lambda *args: records["lock"].append(args) or True,
    )
    return records


@pytest.mark.asyncio
async def test_tick_throttle_lock_and_empty_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = engine_module.Engine()
    engine._last_tick_monotonic = 100.0
    monkeypatch.setenv("ENGINE_TICK_MIN_INTERVAL", "10")
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: 105.0)
    called: list[str] = []
    monkeypatch.setattr(engine_module, "claim_due_jobs", lambda *args, **kwargs: called.append("claim") or [])
    await engine.tick(Bot())
    assert called == []

    engine._last_tick_monotonic = 0.0
    engine._tick_lock = asyncio.Lock()
    await engine._tick_lock.acquire()
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: 20.0)
    await engine.tick(Bot())
    assert called == []
    engine._tick_lock.release()

    records = install_tick_base(monkeypatch, engine)

    async def no_lock(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(engine_module, "acquire_lock", no_lock)
    await engine.tick(Bot())
    assert records["release"] == []

    async def yes_lock(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(engine_module, "acquire_lock", yes_lock)
    monkeypatch.setattr(engine_module, "claim_due_jobs", lambda *args, **kwargs: [])
    await engine.tick(Bot())
    assert records["release"] == ["engine_tick"]


@pytest.mark.asyncio
async def test_tick_success_duplicate_lock_and_bad_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = engine_module.Engine()
    records = install_tick_base(monkeypatch, engine)
    current = Claimed(payload="bad json", job_key="")
    monkeypatch.setattr(engine_module, "claim_due_jobs", lambda *args, **kwargs: [current])

    executed: list[dict] = []

    async def execute(_bot: Any, _job: Any, payload: dict) -> None:
        executed.append(payload)

    monkeypatch.setattr(engine, "_execute_job", execute)
    await engine.tick(Bot())
    assert current.job_key == "legacy:1"
    assert executed == [{}]
    assert records["done"]

    monkeypatch.setattr(engine_module, "mark_delivery_once", lambda *args: False)
    before = len(records["done"])
    await engine.tick(Bot())
    assert len(records["done"]) == before + 1

    monkeypatch.setattr(engine_module, "mark_delivery_once", lambda *args: True)
    monkeypatch.setattr(engine_module, "lock_job", lambda *args: False)
    before = len(records["done"])
    await engine.tick(Bot())
    assert len(records["done"]) == before


@pytest.mark.asyncio
async def test_tick_error_retry_and_dead_letter_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = engine_module.Engine()
    records = install_tick_base(monkeypatch, engine)
    current = Claimed()
    monkeypatch.setattr(engine_module, "claim_due_jobs", lambda *args, **kwargs: [current])

    network_error = type("NetworkFailure", (Exception,), {})
    api_error = type("ApiFailure", (Exception,), {})
    monkeypatch.setattr(engine_module, "TelegramNetworkError", network_error)
    monkeypatch.setattr(engine_module, "TelegramAPIError", api_error)

    async def network(*_args: Any) -> None:
        raise network_error()

    monkeypatch.setattr(engine, "_execute_job", network)
    await engine.tick(Bot())
    assert records["retry"] and any(event[1] == "job_network_retry" for event in records["event"])

    async def api(*_args: Any) -> None:
        raise api_error("api")

    monkeypatch.setattr(engine, "_execute_job", api)
    await engine.tick(Bot())
    assert any(kwargs.get("last_error", "").startswith("TelegramAPIError") for _, kwargs in records["done"])

    async def value(*_args: Any) -> None:
        raise ValueError("value")

    monkeypatch.setattr(engine, "_execute_job", value)
    await engine.tick(Bot())
    assert any(kwargs.get("last_error", "").startswith("ValueError") for _, kwargs in records["done"])

    monkeypatch.setenv("ENGINE_JOB_CRASH_MAX_RETRIES", "3")
    current.retries = 0

    async def crash(*_args: Any) -> None:
        raise RuntimeError("crash")

    monkeypatch.setattr(engine, "_execute_job", crash)
    before_retry = len(records["retry"])
    await engine.tick(Bot())
    assert len(records["retry"]) == before_retry + 1
    assert any(event[1] == "job_crash_retry" for event in records["event"])

    current.retries = 3
    before_done = len(records["done"])
    await engine.tick(Bot())
    assert len(records["done"]) == before_done + 1


@pytest.mark.asyncio
async def test_tick_cancellation_propagates_and_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = engine_module.Engine()
    records = install_tick_base(monkeypatch, engine)
    monkeypatch.setattr(engine_module, "claim_due_jobs", lambda *args, **kwargs: [Claimed()])

    async def cancel(*_args: Any) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(engine, "_execute_job", cancel)
    with pytest.raises(asyncio.CancelledError):
        await engine.tick(Bot())
    assert records["release"] == ["engine_tick"]
