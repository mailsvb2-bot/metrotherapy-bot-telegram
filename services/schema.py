"""DB schema entrypoint.

This module is intentionally thin.

Rationale:
- Previously services/schema.py was ~1000 lines; every small edit risked breaking startup.
- The implementation is now decomposed into:
  - services/schema_core.py (orchestration + helpers)
  - services/schema_tables.py (DDL: tables + columns)
  - services/migrations/* (one-time migrations)
"""

from services.schema_core import init_db, ensure_prod_tables

__all__ = ["init_db", "ensure_prod_tables"]
