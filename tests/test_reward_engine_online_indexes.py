from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from services import reward_engine_indexes
from services import schema


class _FakeSqlTemplate:
    def __init__(self, value: str) -> None:
        self.value = value

    def format(self, identifier: str) -> str:
        return self.value.format(identifier)


class _FakeSqlModule:
    @staticmethod
    def SQL(value: str) -> _FakeSqlTemplate:
        return _FakeSqlTemplate(value)

    @staticmethod
    def Identifier(value: str) -> str:
        return value


class _FakeCursor:
    def __init__(self, connection: "_FakeConnection") -> None:
        self.connection = connection
        self._row: tuple[bool, bool] | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: Any, params: tuple[Any, ...] = ()) -> "_FakeCursor":
        text = str(sql)
        self.connection.executed.append((text, params))
        if "FROM pg_class AS idx" in text:
            name = str(params[0])
            self._row = self.connection.states.get(name)
        elif text.startswith("DROP INDEX CONCURRENTLY IF EXISTS "):
            name = text.rsplit(" ", 1)[1]
            self.connection.states.pop(name, None)
        elif text.startswith("CREATE INDEX CONCURRENTLY "):
            name = text.split()[3]
            if self.connection.build_valid:
                self.connection.states[name] = (True, True)
        return self

    def fetchone(self) -> tuple[bool, bool] | None:
        return self._row


class _FakeConnection:
    def __init__(
        self,
        states: dict[str, tuple[bool, bool]] | None = None,
        *,
        build_valid: bool = True,
    ) -> None:
        self.states = dict(states or {})
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.build_valid = build_valid

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    @contextmanager
    def cursor(self):
        yield _FakeCursor(self)


def _spec(name: str) -> reward_engine_indexes.OnlineIndexSpec:
    return next(spec for spec in reward_engine_indexes.ONLINE_INDEX_SPECS if spec.name == name)


def test_reward_candidate_index_matches_live_query_shape() -> None:
    spec = _spec("idx_events_reward_candidates_v1")
    normalized = " ".join(spec.ddl.split())

    assert "CREATE INDEX CONCURRENTLY" in normalized
    assert "COALESCE(timestamp_utc, ts, created_at), id" in normalized
    assert "INCLUDE (user_id, decision_id, correlation_id)" in normalized
    assert "WHERE name = 'decision_made'" in normalized
    assert "decision_id IS NOT NULL" in normalized
    assert "decision_id <> ''" in normalized


def test_reward_lookup_index_covers_idempotency_pair() -> None:
    spec = _spec("idx_decision_rewards_decision_window_v1")
    normalized = " ".join(spec.ddl.split())

    assert "CREATE INDEX CONCURRENTLY" in normalized
    assert "ON decision_rewards (decision_id, window_sec)" in normalized


def test_bounded_int_accepts_only_configured_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_REWARD_BOUND", "12")
    assert reward_engine_indexes._bounded_int(
        "TEST_REWARD_BOUND",
        7,
        minimum=1,
        maximum=20,
    ) == 12

    monkeypatch.setenv("TEST_REWARD_BOUND", "not-an-int")
    assert reward_engine_indexes._bounded_int(
        "TEST_REWARD_BOUND",
        7,
        minimum=1,
        maximum=20,
    ) == 7

    monkeypatch.setenv("TEST_REWARD_BOUND", "99")
    assert reward_engine_indexes._bounded_int(
        "TEST_REWARD_BOUND",
        7,
        minimum=1,
        maximum=20,
    ) == 7


def test_engine_resolution_is_explicit_then_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METRO_DB_ENGINE", " PostgreSQL ")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert reward_engine_indexes._engine() == "postgresql"

    monkeypatch.delenv("METRO_DB_ENGINE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/metrotherapy")
    assert reward_engine_indexes._engine() == "postgres"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert reward_engine_indexes._engine() == "sqlite"


def test_index_state_reports_missing_and_valid() -> None:
    connection = _FakeConnection()
    with connection.cursor() as cursor:
        assert reward_engine_indexes._index_state(cursor, "missing") is None

    connection.states["ready"] = (True, True)
    with connection.cursor() as cursor:
        assert reward_engine_indexes._index_state(cursor, "ready") == (True, True)


def test_valid_online_index_is_reused() -> None:
    spec = _spec("idx_events_reward_candidates_v1")
    connection = _FakeConnection({spec.name: (True, True)})

    outcome = reward_engine_indexes._ensure_one_index(
        connection,
        spec,
        sql_module=_FakeSqlModule,
    )

    assert outcome == "existing"
    commands = [command for command, _params in connection.executed]
    assert not any(command.startswith("DROP INDEX CONCURRENTLY") for command in commands)
    assert not any(command.startswith("CREATE INDEX CONCURRENTLY") for command in commands)


def test_incomplete_online_index_is_rebuilt() -> None:
    spec = _spec("idx_events_reward_candidates_v1")
    connection = _FakeConnection({spec.name: (False, False)})

    outcome = reward_engine_indexes._ensure_one_index(
        connection,
        spec,
        sql_module=_FakeSqlModule,
    )

    assert outcome == "created"
    assert connection.states[spec.name] == (True, True)
    commands = [command for command, _params in connection.executed]
    assert any(command.startswith("DROP INDEX CONCURRENTLY") for command in commands)
    assert any(command.startswith("CREATE INDEX CONCURRENTLY") for command in commands)


def test_online_index_build_fails_closed_when_verification_is_invalid() -> None:
    spec = _spec("idx_events_reward_candidates_v1")
    connection = _FakeConnection(build_valid=False)

    with pytest.raises(RuntimeError, match="index is not valid after build"):
        reward_engine_indexes._ensure_one_index(
            connection,
            spec,
            sql_module=_FakeSqlModule,
        )


def test_index_manager_skips_non_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert reward_engine_indexes.ensure_reward_engine_indexes() == {
        "engine": "sqlite",
        "status": "skipped",
        "indexes": [],
    }


def test_index_manager_requires_database_url_for_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METRO_DB_ENGINE", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        reward_engine_indexes.ensure_reward_engine_indexes()


def test_index_manager_builds_all_indexes_with_bounded_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    captured: dict[str, Any] = {}

    def fake_connect(
        database_url: str,
        *,
        autocommit: bool,
        connect_timeout: int,
    ) -> _FakeConnection:
        captured.update(
            database_url=database_url,
            autocommit=autocommit,
            connect_timeout=connect_timeout,
        )
        return connection

    monkeypatch.setenv("METRO_DB_ENGINE", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/metrotherapy")
    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT_SEC", "8")
    monkeypatch.setenv("REWARD_INDEX_STATEMENT_TIMEOUT_SEC", "240")
    monkeypatch.setenv("REWARD_INDEX_LOCK_TIMEOUT_SEC", "9")
    monkeypatch.setattr(psycopg, "connect", fake_connect)

    result = reward_engine_indexes.ensure_reward_engine_indexes()

    assert captured == {
        "database_url": "postgresql://example.invalid/metrotherapy",
        "autocommit": True,
        "connect_timeout": 8,
    }
    assert result["engine"] == "postgres"
    assert result["status"] == "ready"
    assert result["indexes"] == [
        {"name": spec.name, "outcome": "created"}
        for spec in reward_engine_indexes.ONLINE_INDEX_SPECS
    ]
    assert set(connection.states) == {spec.name for spec in reward_engine_indexes.ONLINE_INDEX_SPECS}
    assert (
        "SELECT set_config('statement_timeout', %s, false)",
        ("240s",),
    ) in connection.executed
    assert (
        "SELECT set_config('lock_timeout', %s, false)",
        ("9s",),
    ) in connection.executed


def test_schema_init_builds_online_indexes_only_for_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(schema, "_init_db", lambda: calls.append("schema"))
    monkeypatch.setattr(schema, "ensure_reward_engine_indexes", lambda: calls.append("indexes"))

    monkeypatch.setattr(schema, "is_postgres_enabled", lambda: False)
    schema.init_db()
    assert calls == ["schema"]

    calls.clear()
    monkeypatch.setattr(schema, "is_postgres_enabled", lambda: True)
    schema.init_db()
    assert calls == ["schema", "indexes"]
