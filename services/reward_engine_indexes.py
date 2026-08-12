from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_POSTGRES_ENGINES = frozenset({"postgres", "postgresql", "pg"})


@dataclass(frozen=True)
class OnlineIndexSpec:
    name: str
    ddl: str


ONLINE_INDEX_SPECS: tuple[OnlineIndexSpec, ...] = (
    OnlineIndexSpec(
        name="idx_events_reward_candidates_v1",
        ddl="""
        CREATE INDEX CONCURRENTLY idx_events_reward_candidates_v1
        ON events (COALESCE(timestamp_utc, ts, created_at), id)
        INCLUDE (user_id, decision_id, correlation_id)
        WHERE name = 'decision_made'
          AND decision_id IS NOT NULL
          AND decision_id <> ''
        """.strip(),
    ),
    OnlineIndexSpec(
        name="idx_decision_rewards_decision_window_v1",
        ddl="""
        CREATE INDEX CONCURRENTLY idx_decision_rewards_decision_window_v1
        ON decision_rewards (decision_id, window_sec)
        """.strip(),
    ),
)


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, default) or default).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if minimum <= value <= maximum else default


def _engine() -> str:
    explicit = str(os.getenv("METRO_DB_ENGINE") or "").strip().lower()
    if explicit:
        return explicit
    return "postgres" if str(os.getenv("DATABASE_URL") or "").strip() else "sqlite"


def _index_state(cursor: Any, name: str) -> tuple[bool, bool] | None:
    cursor.execute(
        """
        SELECT ix.indisvalid, ix.indisready
        FROM pg_class AS idx
        JOIN pg_namespace AS ns ON ns.oid = idx.relnamespace
        JOIN pg_index AS ix ON ix.indexrelid = idx.oid
        WHERE ns.nspname = current_schema()
          AND idx.relkind = 'i'
          AND idx.relname = %s
        """,
        (name,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return bool(row[0]), bool(row[1])


def _ensure_one_index(
    connection: Any,
    spec: OnlineIndexSpec,
    *,
    sql_module: Any,
    component: str = "RewardEngine",
) -> str:
    with connection.cursor() as cursor:
        state = _index_state(cursor, spec.name)
        if state == (True, True):
            log.info("%s online index already valid: %s", component, spec.name)
            return "existing"

        if state is not None:
            log.warning(
                "Dropping incomplete %s index before retry: %s state=%s",
                component,
                spec.name,
                state,
            )
            cursor.execute(
                sql_module.SQL("DROP INDEX CONCURRENTLY IF EXISTS {}").format(
                    sql_module.Identifier(spec.name)
                )
            )

        log.info("Building %s online index: %s", component, spec.name)
        cursor.execute(spec.ddl)
        verified = _index_state(cursor, spec.name)
        if verified != (True, True):
            raise RuntimeError(
                f"{component} index is not valid after build: {spec.name} state={verified}"
            )
        log.info("%s online index ready: %s", component, spec.name)
        return "created"


def ensure_online_indexes(
    specs: tuple[OnlineIndexSpec, ...],
    *,
    component: str,
    connect_timeout_env: str,
    statement_timeout_env: str,
    lock_timeout_env: str,
    connect_timeout_default: int = 5,
    statement_timeout_default: int = 480,
    lock_timeout_default: int = 10,
) -> dict[str, Any]:
    """Build a bounded set of PostgreSQL indexes without blocking live writes."""

    engine = _engine()
    if engine not in _POSTGRES_ENGINES:
        return {"engine": engine, "status": "skipped", "indexes": []}

    database_url = str(os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError(f"DATABASE_URL is required for {component} PostgreSQL indexes")

    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:  # pragma: no cover - production dependency contract
        raise RuntimeError(f"psycopg is required for {component} PostgreSQL indexes") from exc

    connect_timeout = _bounded_int(
        connect_timeout_env,
        connect_timeout_default,
        minimum=1,
        maximum=60,
    )
    statement_timeout_sec = _bounded_int(
        statement_timeout_env,
        statement_timeout_default,
        minimum=30,
        maximum=540,
    )
    lock_timeout_sec = _bounded_int(
        lock_timeout_env,
        lock_timeout_default,
        minimum=1,
        maximum=60,
    )

    outcomes: list[dict[str, str]] = []
    with psycopg.connect(
        database_url,
        autocommit=True,
        connect_timeout=connect_timeout,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (f"{statement_timeout_sec}s",),
            )
            cursor.execute(
                "SELECT set_config('lock_timeout', %s, false)",
                (f"{lock_timeout_sec}s",),
            )

        for spec in specs:
            outcome = _ensure_one_index(
                connection,
                spec,
                sql_module=sql,
                component=component,
            )
            outcomes.append({"name": spec.name, "outcome": outcome})

    return {"engine": engine, "status": "ready", "indexes": outcomes}


def ensure_reward_engine_indexes() -> dict[str, Any]:
    """Ensure the large-table RewardEngine access path without blocking live writes.

    Production has millions of rows in ``events``. Running the reward candidate
    query without a selective index forces a full-table scan once per reward
    interval and can cross the scheduler's strict five-second owner budget.

    PostgreSQL indexes are therefore built with ``CREATE INDEX CONCURRENTLY`` on
    a dedicated autocommit connection. The operation is bounded and idempotent;
    an interrupted/invalid index is removed and rebuilt on the next attempt.
    SQLite keeps its existing lightweight schema path because local/test databases
    do not need an online-index build protocol.
    """

    return ensure_online_indexes(
        ONLINE_INDEX_SPECS,
        component="RewardEngine",
        connect_timeout_env="REWARD_INDEX_CONNECT_TIMEOUT_SEC",
        statement_timeout_env="REWARD_INDEX_STATEMENT_TIMEOUT_SEC",
        lock_timeout_env="REWARD_INDEX_LOCK_TIMEOUT_SEC",
    )


__all__ = [
    "ONLINE_INDEX_SPECS",
    "OnlineIndexSpec",
    "ensure_online_indexes",
    "ensure_reward_engine_indexes",
]
