from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest
import services.db as db_service

from services import reminder


class Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def fetchall(self) -> list[Any]:
        return self.rows


class Connection:
    def __init__(self, rows: list[Any], error: BaseException | None = None) -> None:
        self.rows = rows
        self.error = error

    def execute(self, _sql: str) -> Result:
        if self.error is not None:
            raise self.error
        return Result(self.rows)


@contextmanager
def db_context(conn: Connection) -> Iterator[Connection]:
    yield conn


class ApiError(Exception):
    pass


class FakeBot:
    def __init__(self, fail_users: set[int] | None = None) -> None:
        self.fail_users = fail_users or set()
        self.messages: list[tuple[int, str, dict[str, Any]]] = []

    async def send_message(self, user_id: int, text: str, **kwargs: Any) -> None:
        self.messages.append((user_id, text, kwargs))
        if user_id in self.fail_users:
            raise ApiError("telegram unavailable")


def install_db(monkeypatch: pytest.MonkeyPatch, conn: Connection) -> None:
    monkeypatch.setattr(db_service, "db", lambda: db_context(conn))


def test_parse_iso_timestamp() -> None:
    value = reminder._parse("2026-07-21T06:00:00+00:00")
    assert value == datetime(2026, 7, 21, 6, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        reminder._parse("not-a-timestamp")


@pytest.mark.asyncio
async def test_reminder_scan_db_failure_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(monkeypatch, Connection([], sqlite3.OperationalError("db down")))
    bot = FakeBot()
    await reminder._funnel_reminder_once(bot)
    assert bot.messages == []


@pytest.mark.asyncio
async def test_reminder_sends_due_steps_and_skips_invalid_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    rows = [{"user_id": user_id} for user_id in range(1, 9)] + [{"bad": 9}]
    install_db(monkeypatch, Connection(rows))
    monkeypatch.setattr(reminder, "utc_now", lambda: now)
    monkeypatch.setattr(reminder, "TelegramAPIError", ApiError)

    timestamps = {
        1: None,
        2: (now - timedelta(hours=2)).isoformat(),
        3: (now - timedelta(hours=25)).isoformat(),
        4: (now - timedelta(hours=25)).isoformat(),
        5: (now - timedelta(hours=25)).isoformat(),
        6: (now - timedelta(minutes=30)).isoformat(),
        7: "bad timestamp",
        8: (now.replace(tzinfo=None) - timedelta(hours=2)).isoformat(),
    }
    monkeypatch.setattr(reminder, "first_ts_for", lambda uid, _event: timestamps[uid])

    completed = {
        (3, "reminded_1"),
        (5, "reminded_1"),
        (5, "deadline_1"),
    }
    monkeypatch.setattr(reminder, "step_done", lambda uid, step: (uid, step) in completed)
    marked: list[tuple[int, str]] = []
    monkeypatch.setattr(reminder, "mark_step", lambda uid, step: marked.append((uid, step)))
    bot = FakeBot()

    await reminder._funnel_reminder_once(bot)

    sent_users = [item[0] for item in bot.messages]
    assert sent_users == [2, 3, 4]
    assert "реальный эффект" in bot.messages[0][1]
    assert "Дедлайн" in bot.messages[1][1]
    assert bot.messages[0][2]["parse_mode"] == "Markdown"
    assert marked == [
        (2, "reminded_1"),
        (3, "deadline_1"),
        (4, "reminded_1"),
    ]


@pytest.mark.asyncio
async def test_reminder_send_and_mark_failures_do_not_stop_other_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    install_db(monkeypatch, Connection([{"user_id": 10}, {"user_id": 11}, {"user_id": 12}]))
    monkeypatch.setattr(reminder, "utc_now", lambda: now)
    monkeypatch.setattr(reminder, "TelegramAPIError", ApiError)
    monkeypatch.setattr(
        reminder,
        "first_ts_for",
        lambda uid, _event: (now - timedelta(hours=2 if uid != 11 else 25)).isoformat(),
    )
    monkeypatch.setattr(reminder, "step_done", lambda uid, step: uid == 11 and step == "reminded_1")
    marks: list[tuple[int, str]] = []

    def mark(uid: int, step: str) -> None:
        if uid == 12:
            raise sqlite3.OperationalError("mark failed")
        marks.append((uid, step))

    monkeypatch.setattr(reminder, "mark_step", mark)
    bot = FakeBot(fail_users={10})

    await reminder._funnel_reminder_once(bot)

    assert [item[0] for item in bot.messages] == [10, 11, 12]
    assert marks == [(11, "deadline_1")]


@pytest.mark.asyncio
async def test_reminder_state_db_failure_isolated_per_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    install_db(monkeypatch, Connection([{"user_id": 20}, {"user_id": 21}]))
    monkeypatch.setattr(reminder, "utc_now", lambda: now)

    def first_ts(uid: int, _event: str) -> str:
        if uid == 20:
            raise sqlite3.OperationalError("event read failed")
        return (now - timedelta(hours=2)).isoformat()

    monkeypatch.setattr(reminder, "first_ts_for", first_ts)
    monkeypatch.setattr(reminder, "step_done", lambda _uid, _step: False)
    marks: list[tuple[int, str]] = []
    monkeypatch.setattr(reminder, "mark_step", lambda uid, step: marks.append((uid, step)))
    bot = FakeBot()

    await reminder._funnel_reminder_once(bot)

    assert [item[0] for item in bot.messages] == [21]
    assert marks == [(21, "reminded_1")]


@pytest.mark.asyncio
async def test_outer_worker_repeats_sweeps(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    sweeps: list[Any] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise RuntimeError("stop loop")

    async def sweep(bot: Any) -> None:
        sweeps.append(bot)

    monkeypatch.setattr(reminder.asyncio, "sleep", sleep)
    monkeypatch.setattr(reminder, "_funnel_reminder_once", sweep)
    bot = object()

    with pytest.raises(RuntimeError, match="stop loop"):
        await reminder.funnel_reminder(bot)

    assert sleeps == [600, 600]
    assert sweeps == [bot]
