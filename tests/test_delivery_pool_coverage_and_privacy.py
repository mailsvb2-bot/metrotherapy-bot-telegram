from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from runtime.messenger_transport_errors import MessengerTransportError
from services.messenger import delivery_pool


class Result:
    def __init__(self, *, row: Any = None, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self.row = row
        self.rows = rows or []
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self.row

    def fetchall(self) -> list[Any]:
        return self.rows


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


def claimed(*, attempts: int = 0) -> Any:
    return delivery_pool.delivery_outbox.ClaimedDelivery(
        id=1,
        platform="max",
        external_user_id="9",
        canonical_user_id=9,
        event_key="event",
        action="text",
        replies_json="[]",
        attempts=attempts,
        lock_token="lock",
    )


def test_bounds_metrics_mapping_and_delete_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUMBER", "bad")
    assert delivery_pool._bounded_int("NUMBER", 5, minimum=2, maximum=8) == 5
    monkeypatch.setenv("NUMBER", "1")
    assert delivery_pool._bounded_int("NUMBER", 5, minimum=2, maximum=8) == 2
    monkeypatch.setenv("NUMBER", "99")
    assert delivery_pool._bounded_int("NUMBER", 5, minimum=2, maximum=8) == 8
    monkeypatch.setenv("MESSENGER_OUTBOX_VK_WORKERS", "3")
    monkeypatch.setenv("MESSENGER_OUTBOX_MAX_WORKERS", "100")
    assert delivery_pool.configured_worker_counts() == {"vk": 3, "max": 32}

    old = dict(delivery_pool._metrics)
    try:
        delivery_pool._metrics["custom"] = 0
        delivery_pool._metric_add("custom", 2)
        delivery_pool._metric_set("label", "ok")
        assert delivery_pool._metrics["custom"] == 2
        assert delivery_pool._metrics["label"] == "ok"
    finally:
        delivery_pool._metrics.clear()
        delivery_pool._metrics.update(old)

    mapped = delivery_pool._claimed_from_row(
        {
            "id": "1", "platform": "vk", "external_user_id": 2,
            "canonical_user_id": "3", "event_key": "e", "action": None,
            "replies_json": None, "attempts": None, "lock_token": "",
        },
        "fallback",
    )
    assert (mapped.id, mapped.action, mapped.replies_json, mapped.lock_token) == (
        1, "", "[]", "fallback"
    )
    tuple_mapped = delivery_pool._claimed_from_row(
        (2, "max", "4", 5, "event", "audio", "[]", 1, "token"), "fallback"
    )
    assert tuple_mapped.lock_token == "token"

    class Conn:
        def execute(self, _query: str, params: Any) -> Result:
            return Result(rowcount=len(params))

    conn = Conn()
    assert delivery_pool._delete_ids(conn, "table", []) == 0
    assert delivery_pool._delete_ids(conn, "table", [1, 2]) == 2
    assert delivery_pool._delete_webhook_keys(conn, []) == 0
    assert delivery_pool._delete_webhook_keys(conn, [("vk", "a"), ("max", "b")]) == 4


def test_claim_stream_head_postgres_and_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        delivery_pool.claim_stream_head(platform="telegram")
    fixed = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(delivery_pool, "utc_now", lambda: fixed)
    monkeypatch.setattr(delivery_pool, "tx", lambda _conn: nullcontext())

    class PostgresConn:
        def __init__(self, row: Any) -> None:
            self.row = row

        def execute(self, _query: str, _params: Any) -> Result:
            return Result(row=self.row)

    monkeypatch.setattr(delivery_pool, "CONFIG", SimpleNamespace(uses_postgres=True))
    pg = PostgresConn((1, "vk", "2", 3, "e", "text", "[]", 0, "token"))
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(pg))
    result = delivery_pool.claim_stream_head(platform=" VK ", lock_ttl_sec=10)
    assert result is not None and result.platform == "vk"
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(PostgresConn(None)))
    assert delivery_pool.claim_stream_head(platform="max") is None

    class SqliteConn:
        def __init__(self, selected: Any, rowcount: int = 1, row: Any = None) -> None:
            self.values = [Result(row=selected), Result(rowcount=rowcount), Result(row=row)]

        def execute(self, _query: str, _params: Any) -> Result:
            return self.values.pop(0)

    monkeypatch.setattr(delivery_pool, "CONFIG", SimpleNamespace(uses_postgres=False))
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(SqliteConn(None)))
    assert delivery_pool.claim_stream_head(platform="vk") is None
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(SqliteConn({"id": 5}, 0)))
    assert delivery_pool.claim_stream_head(platform="vk") is None
    sqlite = SqliteConn(
        (5,), 1, (5, "max", "8", 8, "event", "text", "[]", 2, "token")
    )
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(sqlite))
    result = delivery_pool.claim_stream_head(platform="max")
    assert result is not None and result.id == 5 and result.attempts == 2


def test_cleanup_delivery_history(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(delivery_pool, "utc_now", lambda: fixed)
    monkeypatch.setattr(delivery_pool, "utc_now_iso", lambda: "cleanup-now")
    monkeypatch.setattr(delivery_pool, "tx", lambda _conn: nullcontext())

    class Conn:
        def execute(self, query: str, params: Any) -> Result:
            if "SELECT id" in query and "status='sent'" in query:
                return Result(rows=[{"id": 1}, (2,)])
            if "SELECT id" in query and "status='dead'" in query:
                return Result(rows=[(3,)])
            if "SELECT platform,event_key" in query:
                return Result(rows=[{"platform": "vk", "event_key": "a"}, ("max", "b")])
            return Result(rowcount=len(params) if "id IN" in query else 1)

    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(Conn()))
    old = dict(delivery_pool._metrics)
    try:
        result = delivery_pool.cleanup_delivery_history(
            sent_retention_days=1, dead_retention_days=7,
            webhook_retention_days=2, batch_size=10,
        )
        assert result == delivery_pool.RetentionResult(2, 1, 2)
        assert delivery_pool._metrics["last_cleanup_at"] == "cleanup-now"
    finally:
        delivery_pool._metrics.clear()
        delivery_pool._metrics.update(old)


@pytest.mark.asyncio
async def test_process_item_success_cancel_retry_dead_and_privacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = dict(delivery_pool._metrics)
    rescheduled: list[str] = []
    released: list[int] = []
    try:
        async def success(_item: Any) -> None:
            return None

        monkeypatch.setattr(delivery_pool.delivery_outbox, "_deliver_one", success)
        await delivery_pool._process_item(claimed())
        assert delivery_pool._metrics["delivered"] == old["delivered"] + 1

        async def cancel(_item: Any) -> None:
            raise asyncio.CancelledError

        monkeypatch.setattr(delivery_pool.delivery_outbox, "_deliver_one", cancel)
        monkeypatch.setattr(
            delivery_pool.delivery_outbox, "release_delivery_lease",
            lambda current: released.append(current.id),
        )
        with pytest.raises(asyncio.CancelledError):
            await delivery_pool._process_item(claimed())
        assert released == [1]

        async def secret_failure(_item: Any) -> None:
            raise MessengerTransportError(
                "provider payload token=must-not-leak", code="provider_rejected"
            )

        monkeypatch.setattr(delivery_pool.delivery_outbox, "_deliver_one", secret_failure)
        monkeypatch.setattr(
            delivery_pool.delivery_outbox, "reschedule_delivery",
            lambda _item, error: rescheduled.append(error),
        )
        monkeypatch.setenv("MESSENGER_OUTBOX_MAX_ATTEMPTS", "3")
        await delivery_pool._process_item(claimed(attempts=0))
        assert rescheduled[-1] == "MessengerTransportError:provider_rejected"
        assert "must-not-leak" not in rescheduled[-1]
        await delivery_pool._process_item(claimed(attempts=2))
        assert delivery_pool._metrics["retried"] == old["retried"] + 1
        assert delivery_pool._metrics["dead"] == old["dead"] + 1
    finally:
        delivery_pool._metrics.clear()
        delivery_pool._metrics.update(old)


@pytest.mark.asyncio
async def test_platform_worker_release_idle_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = asyncio.Event()
    released: list[int] = []

    def stopping_claim(**_kwargs: Any) -> Any:
        stop.set()
        return claimed()

    monkeypatch.setattr(delivery_pool, "claim_stream_head", stopping_claim)
    monkeypatch.setattr(
        delivery_pool.delivery_outbox, "release_delivery_lease",
        lambda current: released.append(current.id),
    )
    await delivery_pool._platform_worker(platform="max", worker_no=1, stop_event=stop)
    assert released == [1]

    real_asyncio = asyncio
    calls = 0
    stop = asyncio.Event()

    def claim_sequence(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        raise RuntimeError("tick")

    async def direct_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    async def fake_wait_for(awaitable: Any, *, timeout: float) -> None:
        if hasattr(awaitable, "close"):
            awaitable.close()
        stop.set()
        raise real_asyncio.TimeoutError

    monkeypatch.setattr(delivery_pool, "claim_stream_head", claim_sequence)
    monkeypatch.setattr(
        delivery_pool,
        "asyncio",
        SimpleNamespace(
            to_thread=direct_to_thread,
            wait_for=fake_wait_for,
            TimeoutError=real_asyncio.TimeoutError,
            CancelledError=real_asyncio.CancelledError,
        ),
    )
    await delivery_pool._platform_worker(platform="vk", worker_no=2, stop_event=stop)
    assert calls == 1


@pytest.mark.asyncio
async def test_cleanup_loop_pool_lifecycle_and_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    real_asyncio = asyncio
    stop = asyncio.Event()
    calls = 0

    def cleanup() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("cleanup")

    async def direct_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    async def fake_wait_for(awaitable: Any, *, timeout: float) -> None:
        if hasattr(awaitable, "close"):
            awaitable.close()
        if calls >= 2:
            stop.set()
        raise real_asyncio.TimeoutError

    monkeypatch.setattr(delivery_pool, "cleanup_delivery_history", cleanup)
    monkeypatch.setattr(
        delivery_pool,
        "asyncio",
        SimpleNamespace(
            to_thread=direct_to_thread,
            wait_for=fake_wait_for,
            TimeoutError=real_asyncio.TimeoutError,
            CancelledError=real_asyncio.CancelledError,
        ),
    )
    await delivery_pool._cleanup_loop(stop)
    assert calls == 2
    monkeypatch.undo()

    loop = asyncio.get_running_loop()

    async def short_worker(**_kwargs: Any) -> None:
        await asyncio.sleep(0)

    async def short_cleanup(_stop: asyncio.Event) -> None:
        await asyncio.sleep(0)

    class Manager:
        def create(self, coro: Any, *, name: str) -> asyncio.Task:
            return loop.create_task(coro, name=name)

    monkeypatch.setattr(delivery_pool, "configured_worker_counts", lambda: {"vk": 1, "max": 1})
    monkeypatch.setattr(delivery_pool, "_platform_worker", short_worker)
    monkeypatch.setattr(delivery_pool, "_cleanup_loop", short_cleanup)
    monkeypatch.setattr(delivery_pool, "tm", lambda: Manager())
    await delivery_pool._pool_main(asyncio.Event())
    assert delivery_pool._worker_tasks == []

    existing = loop.create_task(asyncio.sleep(10), name="existing")
    delivery_pool._pool_task = existing
    delivery_pool._pool_stop = asyncio.Event()
    assert delivery_pool.start_delivery_worker() is existing
    await delivery_pool.stop_delivery_worker()
    assert delivery_pool._pool_task is None
    await delivery_pool.stop_delivery_worker()

    live_vk = loop.create_task(asyncio.sleep(10), name="messenger_vk_delivery_worker_1")
    live_max = loop.create_task(asyncio.sleep(10), name="messenger_max_delivery_worker_1")
    done = loop.create_task(asyncio.sleep(0), name="messenger_vk_delivery_worker_2")
    await done
    pool = loop.create_task(asyncio.sleep(10), name="pool")
    delivery_pool._worker_tasks = [live_vk, live_max, done]
    delivery_pool._pool_task = pool
    delivery_pool._pool_stop = asyncio.Event()
    try:
        snapshot = delivery_pool.worker_snapshot()
        assert snapshot["worker_expected"] is True
        assert snapshot["worker_active"] is True
        assert snapshot["vk_workers_active"] == 1
        assert snapshot["max_workers_active"] == 1
    finally:
        for task in (live_vk, live_max, pool):
            task.cancel()
        await asyncio.gather(live_vk, live_max, pool, return_exceptions=True)
        delivery_pool._worker_tasks = []
        delivery_pool._pool_task = None
        delivery_pool._pool_stop = None

    snapshot = delivery_pool.worker_snapshot()
    assert snapshot["worker_expected"] is False
    assert snapshot["worker_active"] is False
    assert snapshot["worker_running"] is True
