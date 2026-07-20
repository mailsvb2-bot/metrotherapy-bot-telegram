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


def item(*, attempts: int = 0, lock_token: str = "lock") -> Any:
    return delivery_pool.delivery_outbox.ClaimedDelivery(
        id=1,
        platform="max",
        external_user_id="9",
        canonical_user_id=9,
        event_key="event",
        action="text",
        replies_json="[]",
        attempts=attempts,
        lock_token=lock_token,
    )


def test_bounded_worker_configuration_and_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NUMBER", raising=False)
    assert delivery_pool._bounded_int("NUMBER", 5, minimum=2, maximum=8) == 5
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


def test_claimed_row_mapping_and_delete_helpers() -> None:
    mapped = delivery_pool._claimed_from_row(
        {
            "id": "1",
            "platform": "vk",
            "external_user_id": 2,
            "canonical_user_id": "3",
            "event_key": "e",
            "action": None,
            "replies_json": None,
            "attempts": None,
            "lock_token": "",
        },
        "fallback",
    )
    assert mapped.id == 1
    assert mapped.platform == "vk"
    assert mapped.external_user_id == "2"
    assert mapped.action == ""
    assert mapped.replies_json == "[]"
    assert mapped.attempts == 0
    assert mapped.lock_token == "fallback"

    tuple_mapped = delivery_pool._claimed_from_row(
        (2, "max", "4", 5, "event", "audio", "[]", 1, "token"), "fallback"
    )
    assert tuple_mapped.id == 2
    assert tuple_mapped.lock_token == "token"

    class Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def execute(self, query: str, params: tuple[Any, ...]) -> Result:
            self.calls.append((query, params))
            return Result(rowcount=-2 if "negative" in query else len(params))

    conn = Conn()
    assert delivery_pool._delete_ids(conn, "table", []) == 0
    assert delivery_pool._delete_ids(conn, "table", [1, 2]) == 2
    assert conn.calls[-1][1] == (1, 2)
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
            self.calls: list[tuple[str, Any]] = []

        def execute(self, query: str, params: Any) -> Result:
            self.calls.append((query, params))
            return Result(row=self.row)

    pg = PostgresConn((1, "vk", "2", 3, "e", "text", "[]", 0, "token"))
    monkeypatch.setattr(delivery_pool, "CONFIG", SimpleNamespace(uses_postgres=True))
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(pg))
    claimed = delivery_pool.claim_stream_head(platform=" VK ", lock_ttl_sec=10)
    assert claimed is not None and claimed.platform == "vk"
    assert "FOR UPDATE SKIP LOCKED" in pg.calls[0][0]

    pg_none = PostgresConn(None)
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(pg_none))
    assert delivery_pool.claim_stream_head(platform="max") is None

    class SqliteConn:
        def __init__(self, *, selected: Any, rowcount: int = 1, claimed_row: Any = None) -> None:
            self.selected = selected
            self.rowcount = rowcount
            self.claimed_row = claimed_row
            self.count = 0

        def execute(self, query: str, _params: Any) -> Result:
            self.count += 1
            if self.count == 1:
                return Result(row=self.selected)
            if self.count == 2:
                return Result(rowcount=self.rowcount)
            return Result(row=self.claimed_row)

    monkeypatch.setattr(delivery_pool, "CONFIG", SimpleNamespace(uses_postgres=False))
    no_selected = SqliteConn(selected=None)
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(no_selected))
    assert delivery_pool.claim_stream_head(platform="vk") is None

    lost = SqliteConn(selected={"id": 5}, rowcount=0)
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(lost))
    assert delivery_pool.claim_stream_head(platform="vk") is None

    sqlite = SqliteConn(
        selected=(5,),
        rowcount=1,
        claimed_row=(5, "max", "8", 8, "event", "text", "[]", 2, "token"),
    )
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(sqlite))
    claimed = delivery_pool.claim_stream_head(platform="max")
    assert claimed is not None and claimed.id == 5 and claimed.attempts == 2


def test_cleanup_delivery_history(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(delivery_pool, "utc_now", lambda: fixed)
    monkeypatch.setattr(delivery_pool, "utc_now_iso", lambda: "cleanup-now")
    monkeypatch.setattr(delivery_pool, "tx", lambda _conn: nullcontext())

    class Conn:
        def __init__(self) -> None:
            self.deleted: list[tuple[str, Any]] = []

        def execute(self, query: str, params: Any) -> Result:
            if "SELECT id" in query and "status='sent'" in query:
                return Result(rows=[{"id": 1}, (2,)])
            if "SELECT id" in query and "status='dead'" in query:
                return Result(rows=[(3,)])
            if "SELECT platform,event_key" in query:
                return Result(rows=[{"platform": "vk", "event_key": "a"}, ("max", "b")])
            self.deleted.append((query, params))
            return Result(rowcount=len(params) if "id IN" in query else 1)

    conn = Conn()
    monkeypatch.setattr(delivery_pool, "db", lambda: DbContext(conn))
    old = dict(delivery_pool._metrics)
    try:
        result = delivery_pool.cleanup_delivery_history(
            sent_retention_days=1,
            dead_retention_days=7,
            webhook_retention_days=2,
            batch_size=10,
        )
        assert result == delivery_pool.RetentionResult(
            sent_deleted=2, dead_deleted=1, webhook_deleted=2
        )
        assert delivery_pool._metrics["sent_deleted"] == old["sent_deleted"] + 2
        assert delivery_pool._metrics["dead_deleted"] == old["dead_deleted"] + 1
        assert delivery_pool._metrics["webhook_deleted"] == old["webhook_deleted"] + 2
        assert delivery_pool._metrics["cleanup_runs"] == old["cleanup_runs"] + 1
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
        await delivery_pool._process_item(item())
        assert delivery_pool._metrics["delivered"] == old["delivered"] + 1

        async def cancelled(_item: Any) -> None:
            raise asyncio.CancelledError

        monkeypatch.setattr(delivery_pool.delivery_outbox, "_deliver_one", cancelled)
        monkeypatch.setattr(
            delivery_pool.delivery_outbox,
            "release_delivery_lease",
            lambda claimed: released.append(claimed.id),
        )
        with pytest.raises(asyncio.CancelledError):
            await delivery_pool._process_item(item())
        assert released == [1]

        async def secret_failure(_item: Any) -> None:
            raise MessengerTransportError(
                "provider payload token=must-not-leak", code="provider_rejected"
            )

        monkeypatch.setattr(delivery_pool.delivery_outbox, "_deliver_one", secret_failure)
        monkeypatch.setattr(
            delivery_pool.delivery_outbox,
            "reschedule_delivery",
            lambda _item, error: rescheduled.append(error),
        )
        monkeypatch.setenv("MESSENGER_OUTBOX_MAX_ATTEMPTS", "3")
        await delivery_pool._process_item(item(attempts=0))
        assert rescheduled[-1] == "MessengerTransportError:provider_rejected"
        assert "must-not-leak" not in rescheduled[-1]
        assert delivery_pool._metrics["retried"] == old["retried"] + 1

        await delivery_pool._process_item(item(attempts=2))
        assert delivery_pool._metrics["dead"] == old["dead"] + 1
    finally:
        delivery_pool._metrics.clear()
        delivery_pool._metrics.update(old)


@pytest.mark.asyncio
async def test_platform_worker_releases_claim_when_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    claimed = item()
    released: list[int] = []

    def claim(**_kwargs: Any) -> Any:
        stop.set()
        return claimed

    monkeypatch.setattr(delivery_pool, "claim_stream_head", claim)
    monkeypatch.setattr(
        delivery_pool.delivery_outbox,
        "release_delivery_lease",
        lambda current: released.append(current.id),
    )
    await delivery_pool._platform_worker(platform="max", worker_no=1, stop_event=stop)
    assert released == [1]


@pytest.mark.asyncio
async def test_platform_worker_idle_and_tick_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = asyncio.Event()
    calls = 0

    def claim(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        raise RuntimeError("tick")

    async def wait_for(_awaitable: Any, *, timeout: float) -> None:
        assert timeout >= 0.1
        stop.set()
        raise asyncio.TimeoutError

    monkeypatch.setenv("MESSENGER_OUTBOX_IDLE_SLEEP_SEC", "bad")
    monkeypatch.setattr(delivery_pool, "claim_stream_head", claim)
    monkeypatch.setattr(delivery_pool.asyncio, "wait_for", wait_for)
    await delivery_pool._platform_worker(platform="vk", worker_no=2, stop_event=stop)
    assert calls == 1

    stop.clear()
    calls = 1
    await delivery_pool._platform_worker(platform="vk", worker_no=2, stop_event=stop)
    assert calls == 2


@pytest.mark.asyncio
async def test_cleanup_loop_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = asyncio.Event()
    calls = 0

    def cleanup() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("cleanup")

    async def wait_for(_awaitable: Any, *, timeout: float) -> None:
        assert timeout >= 60
        if calls >= 2:
            stop.set()
        raise asyncio.TimeoutError

    monkeypatch.setattr(delivery_pool, "cleanup_delivery_history", cleanup)
    monkeypatch.setattr(delivery_pool.asyncio, "wait_for", wait_for)
    await delivery_pool._cleanup_loop(stop)
    assert calls == 2


@pytest.mark.asyncio
async def test_pool_lifecycle_and_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    async def short_worker(**_kwargs: Any) -> None:
        await asyncio.sleep(0)

    async def short_cleanup(_stop: asyncio.Event) -> None:
        await asyncio.sleep(0)

    class Manager:
        def create(self, coro: Any, *, name: str) -> asyncio.Task:
            return asyncio.create_task(coro, name=name)

    monkeypatch.setattr(delivery_pool, "configured_worker_counts", lambda: {"vk": 1, "max": 1})
    monkeypatch.setattr(delivery_pool, "_platform_worker", short_worker)
    monkeypatch.setattr(delivery_pool, "_cleanup_loop", short_cleanup)
    monkeypatch.setattr(delivery_pool, "tm", lambda: Manager())
    await delivery_pool._pool_main(asyncio.Event())
    assert delivery_pool._worker_tasks == []

    existing = asyncio.create_task(asyncio.sleep(10), name="existing")
    delivery_pool._pool_task = existing
    delivery_pool._pool_stop = asyncio.Event()
    try:
        assert delivery_pool.start_delivery_worker() is existing
        await delivery_pool.stop_delivery_worker()
        assert delivery_pool._pool_task is None
        assert delivery_pool._pool_stop is None
        await delivery_pool.stop_delivery_worker()
    finally:
        if not existing.done():
            existing.cancel()

    live_vk = asyncio.create_task(asyncio.sleep(10), name="messenger_vk_delivery_worker_1")
    live_max = asyncio.create_task(asyncio.sleep(10), name="messenger_max_delivery_worker_1")
    done = asyncio.create_task(asyncio.sleep(0), name="messenger_vk_delivery_worker_2")
    await done
    pool = asyncio.create_task(asyncio.sleep(10), name="pool")
    delivery_pool._worker_tasks = [live_vk, live_max, done]
    delivery_pool._pool_task = pool
    delivery_pool._pool_stop = asyncio.Event()
    try:
        snapshot = delivery_pool.worker_snapshot()
        assert snapshot["worker_expected"] is True
        assert snapshot["worker_active"] is True
        assert snapshot["worker_running"] is True
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
