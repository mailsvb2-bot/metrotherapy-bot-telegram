from __future__ import annotations

from contextlib import contextmanager
from typing import Any

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
        self.connection.executed.append(text)
        if "FROM pg_class AS idx" in text:
            name = str(params[0])
            self._row = self.connection.states.get(name)
        elif text.startswith("DROP INDEX CONCURRENTLY IF EXISTS "):
            name = text.rsplit(" ", 1)[1]
            self.connection.states.pop(name, None)
        elif text.startswith("CREATE INDEX CONCURRENTLY "):
            name = text.split()[3]
            self.connection.states[name] = (True, True)
        return self

    def fetchone(self) -> tuple[bool, bool] | None:
        return self._row


class _FakeConnection:
    def __init__(self, states: dict[str, tuple[bool, bool]] | None = None) -> None:
        self.states = dict(states or {})
        self.executed: list[str] = []

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
    assert any(command.startswith("DROP INDEX CONCURRENTLY") for command in connection.executed)
    assert any(command.startswith("CREATE INDEX CONCURRENTLY") for command in connection.executed)


def test_schema_init_builds_online_indexes_only_for_postgres(monkeypatch) -> None:
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
