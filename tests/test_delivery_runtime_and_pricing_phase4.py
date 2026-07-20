from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from runtime import telegram_action_runner as action_runner
from services import pricing_read, pricing_sync, send_queue, session_timers


class AnswerTarget:
    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text: str) -> str:
        self.answers.append(text)
        return f"sent:{text}"


@pytest.mark.asyncio
async def test_telegram_action_runner_message_paths() -> None:
    target = AnswerTarget()
    runner = action_runner.TelegramActionRunner(bot=SimpleNamespace(), message=target)

    result = await runner.run({"type": "safe_content"})
    assert result.startswith("sent:")
    assert "Временно недоступно" in target.answers[-1]

    result = await runner.run({"type": "send_text", "text": 123})
    assert result == "sent:123"
    assert target.answers[-1] == "123"

    assert await runner.run({"type": "unknown"}) is None
    assert await runner.run({}) is None


@pytest.mark.asyncio
async def test_telegram_action_runner_callback_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMessage:
        pass

    monkeypatch.setattr(action_runner, "Message", FakeMessage)
    fake_message = FakeMessage()
    assert action_runner._callback_message(SimpleNamespace(message=fake_message)) is fake_message
    assert action_runner._callback_message(SimpleNamespace(message=object())) is None

    target = AnswerTarget()
    cb = SimpleNamespace(message=object())
    runner = action_runner.TelegramActionRunner(bot=SimpleNamespace(), cb=cb)

    monkeypatch.setattr(action_runner, "_callback_message", lambda _cb: target)
    assert "Временно недоступно" in await runner.run({"type": "safe_content"})
    assert await runner.run({"type": "send_text", "text": "hello"}) == "sent:hello"

    monkeypatch.setattr(action_runner, "_callback_message", lambda _cb: None)
    assert await runner.run({"type": "safe_content"}) is None
    assert await runner.run({"type": "send_text", "text": "hello"}) is None


def test_send_queue_reuses_one_semaphore_per_user() -> None:
    send_queue._queues.clear()
    first = send_queue.queue(7)
    again = send_queue.queue(7)
    other = send_queue.queue(8)

    assert isinstance(first, asyncio.Semaphore)
    assert first is again
    assert first is not other


def test_session_timers_fail_fast_and_log(caplog: pytest.LogCaptureFixture) -> None:
    for callback in (session_timers.add_job, session_timers.cancel_job, session_timers.tick_jobs):
        with pytest.raises(RuntimeError, match="session_timers is deprecated"):
            callback("legacy")
    assert "Use services.jobs + engine.tick only" in caplog.text


def test_pricing_title_normalization_and_suggestions() -> None:
    plans = [
        {"title": "Полный маршрут — 60 практик"},
        {"title": "Стартовый пакет"},
        {"title": ""},
        {"title": "Стартовый пакет"},
    ]
    assert pricing_read._norm_title("  ПОЛНЫЙ  маршрут–60 практик ") == "полный маршрут - 60 практик"
    suggestions = pricing_read.suggest_plan_titles("полный маршрут 60", plans=plans, limit=3)
    assert suggestions[0] == "Полный маршрут — 60 практик"
    assert suggestions.count("Стартовый пакет") == 1


class RowsCursor:
    def __init__(self, rows: list[tuple[Any, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, Any]]:
        return self._rows


class PricingConn:
    def __init__(self, rows: list[tuple[Any, Any]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def execute(self, query: str) -> RowsCursor:
        self.queries.append(query)
        return RowsCursor(self.rows)


@contextmanager
def db_context(conn: PricingConn):
    yield conn


def test_read_plans_from_explicit_and_default_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = PricingConn([("start", 2499), ("full", "4199")])
    assert pricing_read.read_plans(conn) == {"start": 2499, "full": 4199}
    assert "is_active=1" in conn.queries[-1]

    other = PricingConn([("personal", 24870)])
    monkeypatch.setattr(pricing_read, "db", lambda: db_context(other))
    assert pricing_read.read_plans() == {"personal": 24870}


def test_pricing_sync_is_deprecated_noop(caplog: pytest.LogCaptureFixture) -> None:
    pricing_sync.write_tariffs_file({"start": 2499})
    assert pricing_sync.sync_tariffs_to_db(object()) is False
    assert "tariffs file is deprecated" in caplog.text
