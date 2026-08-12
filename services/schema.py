"""DB schema entrypoint.

This module is intentionally thin.

Rationale:
- Previously services/schema.py was ~1000 lines; every small edit risked breaking startup.
- The implementation is now decomposed into:
  - services/schema_core.py (orchestration + helpers)
  - services/schema_tables.py (DDL: tables + columns)
  - services/migrations/* (one-time migrations)
"""

from services.db.runtime import is_postgres_enabled
from services.funnel_analytics_indexes import ensure_funnel_analytics_indexes
from services.reward_engine_indexes import ensure_reward_engine_indexes
from services.schema_core import ensure_prod_tables, init_db as _init_db


def init_db() -> None:
    """Initialize the schema and establish production-only online access paths."""

    _init_db()
    if is_postgres_enabled():
        ensure_reward_engine_indexes()
        ensure_funnel_analytics_indexes()


__all__ = ["init_db", "ensure_prod_tables"]
