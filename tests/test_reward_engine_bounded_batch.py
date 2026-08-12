from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any

import pytest

from core.ai import reward_engine


class _Cursor:
    def __init__(self, *, rows: list[tuple[Any, ...]] | None = None, row: tuple[Any, ...] | None = None) -> None:
        self._rows = rows or []
        self._row = row
        self.rowcount = 1

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _Connection:
    def __init__(self, candidates: list[tuple[Any, ...]]) -> None:
        self.candidates = candidates
        self.executed: list[str] = []
        self.inserted = 0
        self.committed = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        del params
        normalized = " ".join(sql.split())
        self.executed.append(normalized)
        if "FROM events AS e" in normalized and "NOT EXISTS" in normalized:
            marker = " LIMIT "
            limit = int(normalized.rsplit(marker, 1)[1])
            return _Cursor(rows=self.candidates[:limit])
        if "SUM(amount)" in normalized:
            return _Cursor(row=(0,))
        if "SUM(CASE" in normalized:
            return _Cursor(row=(0,))
        if "AVG(rating)" in normalized:
            return _Cursor(row=(0,))
        if normalized.startswith("SELECT COUNT(*) FROM events"):
            return _Cursor(row=(0,))
        if "MAX(idx)" in normalized:
            return _Cursor(row=(0,))
        if normalized.startswith("INSERT INTO decision_rewards"):
            self.inserted += 1
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {normalized}")

    def commit(self) -> None:
        self.committed = True


class _CandidateQueryErrorConnection(_Connection):
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        normalized = " ".join(sql.split())
        if "FROM events AS e" in normalized and "NOT EXISTS" in normalized:
            raise sqlite3.OperationalError("synthetic candidate query failure")
        return super().execute(sql, params)


class _AggregateErrorConnection(_Connection):
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        normalized = " ".join(sql.split())
        aggregate_markers = (
            "SUM(amount)",
            "SUM(CASE",
            "AVG(rating)",
            "SELECT COUNT(*) FROM events",
            "MAX(idx)",
        )
        if any(marker in normalized for marker in aggregate_markers):
            raise sqlite3.OperationalError("synthetic aggregate failure")
        return super().execute(sql, params)


def _candidate(index: int) -> tuple[Any, ...]:
    timestamp = datetime(2026, 7, 27, 12, index, tzinfo=timezone.utc).isoformat()
    return (index, 1000 + index, f"decision-{index}", f"corr-{index}", timestamp)


def test_decision_time_handles_malformed_naive_and_aware_values() -> None:
    fallback = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)

    assert reward_engine._decision_time(object(), fallback=fallback) == fallback

    naive = reward_engine._decision_time("2026-07-27T12:01:00", fallback=fallback)
    assert naive.tzinfo == timezone.utc
    assert naive.hour == 12

    aware = reward_engine._decision_time("2026-07-27T12:02:00+03:00", fallback=fallback)
    assert aware.utcoffset() is not None
    assert aware.utcoffset().total_seconds() == 3 * 60 * 60


def test_reward_engine_filters_in_sql_and_stops_at_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection([_candidate(1), _candidate(2), _candidate(3)])
    monotonic_values = iter((0.0, 0.1, 2.0))

    monkeypatch.setattr(reward_engine, "db", lambda: nullcontext(connection))
    monkeypatch.setattr(reward_engine.time, "monotonic", lambda: next(monotonic_values))

    written = reward_engine.compute_and_store_rewards(
        batch_size=3,
        max_runtime_sec=1.0,
    )

    assert written == 1
    assert connection.inserted == 1
    assert connection.committed is True
    candidate_sql = connection.executed[0]
    assert "NOT EXISTS" in candidate_sql
    assert "ORDER BY e.id ASC" in candidate_sql
    assert candidate_sql.endswith("LIMIT 3")
    assert not any(sql.startswith("SELECT 1 FROM decision_rewards") for sql in connection.executed)


def test_reward_engine_applies_batch_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection([_candidate(1), _candidate(2), _candidate(3)])

    monkeypatch.setattr(reward_engine, "db", lambda: nullcontext(connection))
    monkeypatch.setattr(reward_engine.time, "monotonic", lambda: 0.0)

    written = reward_engine.compute_and_store_rewards(
        batch_size=2,
        max_runtime_sec=1.0,
    )

    assert written == 2
    assert connection.inserted == 2
    assert connection.executed[0].endswith("LIMIT 2")


def test_reward_engine_candidate_query_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _CandidateQueryErrorConnection([])

    monkeypatch.setattr(reward_engine, "db", lambda: nullcontext(connection))
    monkeypatch.setattr(reward_engine.time, "monotonic", lambda: 0.0)

    assert reward_engine.compute_and_store_rewards(batch_size=1, max_runtime_sec=1.0) == 0
    assert connection.inserted == 0
    assert connection.committed is False


def test_reward_engine_skips_malformed_candidate_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    malformed = (1, "not-an-int", "decision-bad", None, "2026-07-27T12:01:00+00:00")
    connection = _Connection([malformed])

    monkeypatch.setattr(reward_engine, "db", lambda: nullcontext(connection))
    monkeypatch.setattr(reward_engine.time, "monotonic", lambda: 0.0)

    assert reward_engine.compute_and_store_rewards(batch_size=1, max_runtime_sec=1.0) == 0
    assert connection.inserted == 0
    assert connection.committed is True


def test_reward_engine_contains_optional_aggregate_db_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _AggregateErrorConnection([_candidate(1)])

    monkeypatch.setattr(reward_engine, "db", lambda: nullcontext(connection))
    monkeypatch.setattr(reward_engine.time, "monotonic", lambda: 0.0)

    written = reward_engine.compute_and_store_rewards(batch_size=1, max_runtime_sec=1.0)

    assert written == 1
    assert connection.inserted == 1
    assert connection.committed is True
