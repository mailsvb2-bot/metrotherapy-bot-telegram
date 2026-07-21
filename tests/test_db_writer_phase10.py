from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services import db_writer
from services.db import core as db_core


class FakeTask:
    def __init__(self, *, done: bool = False) -> None:
        self.done_value = done
        self.cancel_calls = 0

    def done(self) -> bool:
        return self.done_value

    def cancel(self) -> None:
        self.cancel_calls += 1


class PgCursor:
    def __init__(
        self,
        *,
        one: Any = None,
        all_rows: list[Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.one = one
        self.all_rows = list(all_rows or [])
        self.error = error
        self.execute_calls: list[tuple[str, Any]] = []
        self.many_calls: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any) -> None:
        self.execute_calls.append((sql, params))
        if self.error is not None:
            raise self.error

    def executemany(self, sql: str, params: Any) -> None:
        self.many_calls.append((sql, params))
        if self.error is not None:
            raise self.error

    def fetchone(self) -> Any:
        return self.one

    def fetchall(self) -> list[Any]:
        return list(self.all_rows)


class PgConnection:
    def __init__(
        self,
        cursor: PgCursor,
        *,
        rollback_error: BaseException | None = None,
    ) -> None:
        self.pg_cursor = cursor
        self.rollback_error = rollback_error
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def cursor(self) -> PgCursor:
        return self.pg_cursor

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self) -> None:
        self.close_calls += 1


@pytest.fixture(autouse=True)
def reset_writer_state() -> None:
    db_writer._queue = None
    db_writer._task = None
    yield
    task = db_writer._task
    if task is not None and hasattr(task, "cancel"):
        task.cancel()
    db_writer._queue = None
    db_writer._task = None


def test_write_sql_detection() -> None:
    assert db_writer._is_write_sql("") is False
    assert db_writer._is_write_sql("-- only comment") is False
    assert db_writer._is_write_sql("-- comment\n INSERT INTO x VALUES(1)") is True
    assert db_writer._is_write_sql("WITH q AS (SELECT 1) SELECT * FROM q") is False
    assert db_writer._is_write_sql("WITH q AS (SELECT 1) UPDATE x SET a=1") is True
    assert db_writer._is_write_sql("nonsense") is False
    for sql in (
        "INSERT x",
        "UPDATE x",
        "DELETE x",
        "REPLACE x",
        "CREATE TABLE x(a)",
        "DROP TABLE x",
        "ALTER TABLE x ADD a",
        "VACUUM",
        "PRAGMA x",
        "BEGIN",
        "COMMIT",
    ):
        assert db_writer._is_write_sql(sql) is True


def test_finish_future_result_exception_and_cancelled() -> None:
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        db_writer._finish_future(fut, result=7)
        assert fut.result() == 7
        db_writer._finish_future(fut, result=8)
        assert fut.result() == 7

        failed = loop.create_future()
        error = RuntimeError("failed")
        db_writer._finish_future(failed, exception=error)
        assert failed.exception() is error

        cancelled = loop.create_future()
        cancelled.cancel()
        db_writer._finish_future(cancelled, result=1)
        assert cancelled.cancelled() is True
    finally:
        loop.close()


def test_start_writer_postgres_and_existing_task(monkeypatch: pytest.MonkeyPatch) -> None:
    db_writer._queue = asyncio.Queue()
    db_writer._task = FakeTask()
    monkeypatch.setattr(db_writer, "is_postgres_enabled", lambda: True)
    db_writer.start_db_writer()
    assert db_writer._queue is None
    assert db_writer._task is None

    existing = FakeTask(done=False)
    db_writer._task = existing
    db_writer._queue = asyncio.Queue()
    monkeypatch.setattr(db_writer, "is_postgres_enabled", lambda: False)
    db_writer.start_db_writer()
    assert db_writer._task is existing


@pytest.mark.asyncio
async def test_start_writer_task_manager_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_writer, "is_postgres_enabled", lambda: False)
    created: list[Any] = []

    class Manager:
        def create(self, coro):
            created.append(coro)
            task = asyncio.get_running_loop().create_task(coro)
            return task

    import services.bg as bg

    monkeypatch.setattr(bg, "tm", lambda: Manager())
    monkeypatch.setattr(db_writer, "_writer_loop", lambda: asyncio.sleep(3600))
    db_writer.start_db_writer()
    assert created
    await db_writer.stop_db_writer(drain=False)

    def unavailable():
        raise RuntimeError("unavailable")

    monkeypatch.setattr(bg, "tm", unavailable)
    db_writer.start_db_writer()
    assert db_writer._task is not None
    await db_writer.stop_db_writer(drain=False)


@pytest.mark.asyncio
async def test_stop_writer_no_task_and_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    await db_writer.stop_db_writer()

    queue: asyncio.Queue[Any] = asyncio.Queue()
    await queue.put(object())

    async def worker() -> None:
        await asyncio.sleep(0)
        queue.get_nowait()
        queue.task_done()
        await asyncio.sleep(3600)

    db_writer._queue = queue
    db_writer._task = asyncio.get_running_loop().create_task(worker())
    await db_writer.stop_db_writer(drain=True)
    assert db_writer._queue is None
    assert db_writer._task is None


@pytest.mark.asyncio
async def test_enqueue_postgres_route_and_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_writer, "is_postgres_enabled", lambda: True)
    calls: list[Any] = []

    async def execute(sql, params, **kwargs):
        calls.append((sql, params, kwargs))
        return "result"

    monkeypatch.setattr(db_writer, "_execute_postgres_job", execute)
    assert await db_writer.enqueue("SELECT 1", (1,), fetchone=True) == "result"
    assert calls[0][2]["fetchone"] is True

    monkeypatch.setattr(db_writer, "is_postgres_enabled", lambda: False)
    with pytest.raises(RuntimeError, match="not running"):
        await db_writer.enqueue("SELECT 1")


@pytest.mark.asyncio
async def test_enqueue_many_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    async def enqueue(sql, params, **kwargs):
        calls.append((sql, params, kwargs))
        return None

    monkeypatch.setattr(db_writer, "enqueue", enqueue)
    await db_writer.enqueue_many("INSERT x", [(1,), (2,)])
    assert calls == [("INSERT x", ((1,), (2,)), {"many": True})]


async def run_inline(fn):
    return fn()


@pytest.mark.asyncio
async def test_execute_postgres_job_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_writer.asyncio, "to_thread", run_inline)

    cursor = PgCursor()
    conn = PgConnection(cursor)
    monkeypatch.setattr(db_core, "get_connection", lambda: conn)
    assert await db_writer._execute_postgres_job("INSERT INTO x VALUES(?)", [(1,), (2,)], many=True) is None
    assert cursor.many_calls
    assert conn.commit_calls == 1
    assert conn.close_calls == 1

    cursor = PgCursor(one={"id": 1})
    conn = PgConnection(cursor)
    monkeypatch.setattr(db_core, "get_connection", lambda: conn)
    assert await db_writer._execute_postgres_job("SELECT 1", fetchone=True) == {"id": 1}
    assert conn.commit_calls == 0

    cursor = PgCursor(all_rows=[{"id": 1}])
    conn = PgConnection(cursor)
    monkeypatch.setattr(db_core, "get_connection", lambda: conn)
    assert await db_writer._execute_postgres_job("UPDATE x SET a=1 RETURNING id", fetchall=True) == [{"id": 1}]
    assert conn.commit_calls == 1

    cursor = PgCursor()
    conn = PgConnection(cursor)
    monkeypatch.setattr(db_core, "get_connection", lambda: conn)
    assert await db_writer._execute_postgres_job("SELECT 1") is None
    assert conn.commit_calls == 0


@pytest.mark.asyncio
async def test_execute_postgres_job_rollback_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_writer.asyncio, "to_thread", run_inline)
    cursor = PgCursor(error=RuntimeError("query"))
    conn = PgConnection(cursor)
    monkeypatch.setattr(db_core, "get_connection", lambda: conn)
    with pytest.raises(RuntimeError, match="query"):
        await db_writer._execute_postgres_job("UPDATE x SET a=1")
    assert conn.rollback_calls == 1
    assert conn.close_calls == 1

    cursor = PgCursor(error=RuntimeError("query"))
    conn = PgConnection(cursor, rollback_error=RuntimeError("rollback"))
    monkeypatch.setattr(db_core, "get_connection", lambda: conn)
    with pytest.raises(RuntimeError, match="query"):
        await db_writer._execute_postgres_job("UPDATE x SET a=1")
    assert conn.close_calls == 1


@pytest.mark.asyncio
async def test_sqlite_writer_end_to_end_and_cancelled_future_resilience(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(db_writer, "DB_PATH", tmp_path / "writer.db")
    monkeypatch.setattr(db_writer, "is_postgres_enabled", lambda: False)
    db_writer._queue = asyncio.Queue()
    db_writer._task = asyncio.get_running_loop().create_task(db_writer._writer_loop())

    await db_writer.enqueue("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
    await db_writer.enqueue_many("INSERT INTO sample(value) VALUES(?)", [("a",), ("b",)])
    row = await db_writer.enqueue("SELECT value FROM sample WHERE id=?", (1,), fetchone=True)
    assert row["value"] == "a"
    rows = await db_writer.enqueue("SELECT value FROM sample ORDER BY id", fetchall=True)
    assert [row["value"] for row in rows] == ["a", "b"]

    cancelled = asyncio.get_running_loop().create_future()
    cancelled.cancel()
    await db_writer._queue.put(
        db_writer.DbJob(
            sql="INSERT INTO sample(value) VALUES(?)",
            params=("cancelled-caller",),
            many=False,
            fetchone=False,
            fetchall=False,
            fut=cancelled,
        )
    )
    await db_writer._queue.join()
    assert db_writer._task.done() is False

    count = await db_writer.enqueue("SELECT COUNT(*) AS c FROM sample", fetchone=True)
    assert count["c"] == 3

    with pytest.raises(sqlite3.Error):
        await db_writer.enqueue("INSERT INTO missing_table VALUES(1)")
    assert db_writer._task.done() is False

    await db_writer.stop_db_writer(drain=True)
