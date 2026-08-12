from __future__ import annotations

from services.reward_engine_indexes import OnlineIndexSpec, ensure_online_indexes

COMMERCIAL_FUNNEL_EVENT_NAMES: tuple[str, ...] = (
    "demo_sent",
    "demo_ack",
    "funnel_nudge_sent",
    "funnel_offer_sent",
    "funnel_deadline_sent",
    "funnel_lastcall_sent",
    "view_tariffs",
    "invoice_created",
    "payment_started",
    "invoice_paid",
    "payment_success",
    "successful_payment",
    "sub_paid",
)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL = ", ".join(
    _sql_literal(name) for name in COMMERCIAL_FUNNEL_EVENT_NAMES
)

ONLINE_INDEX_SPECS: tuple[OnlineIndexSpec, ...] = (
    OnlineIndexSpec(
        name="idx_events_commercial_funnel_v1",
        ddl=f"""
        CREATE INDEX CONCURRENTLY idx_events_commercial_funnel_v1
        ON events (name, created_at, user_id)
        WHERE name IN ({COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL})
        """.strip(),
    ),
)


def ensure_funnel_analytics_indexes() -> dict[str, object]:
    """Ensure the selective production access path used by funnel analytics.

    The production ``events`` table is large. Commercial funnel reports must not
    perform repeated full-table scans just to count a small, stable set of user
    journey events. The partial index keeps the access path bounded to commercial
    telemetry and is created concurrently so normal writes can continue.
    """

    return ensure_online_indexes(
        ONLINE_INDEX_SPECS,
        component="FunnelAnalytics",
        connect_timeout_env="FUNNEL_INDEX_CONNECT_TIMEOUT_SEC",
        statement_timeout_env="FUNNEL_INDEX_STATEMENT_TIMEOUT_SEC",
        lock_timeout_env="FUNNEL_INDEX_LOCK_TIMEOUT_SEC",
    )


__all__ = [
    "COMMERCIAL_FUNNEL_EVENT_NAMES",
    "COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL",
    "ONLINE_INDEX_SPECS",
    "ensure_funnel_analytics_indexes",
]
