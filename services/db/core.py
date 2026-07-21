from __future__ import annotations

"""Canonical database compatibility facade.

The established database implementation lives in :mod:`services.db.core_legacy`
unchanged. This facade installs the narrow fail-closed SQLite-to-PostgreSQL SQL
translation guards and re-exports the complete historical module surface.
Keeping the guard in this importable facade makes it deterministic across normal
imports and ``importlib.reload`` without rewriting working database behavior.
"""

import sys as _sys
import types as _types

from services.db import core_legacy as _legacy
from services.db.sql_compat_guard import (
    replace_qmark_placeholders,
    translate_sqlite_master_tables_query,
    validate_sqlite_compat_statement,
)


_original_translate = getattr(
    _legacy,
    "_UNGUARDED_TRANSLATE_SQL_FOR_POSTGRES",
    None,
)
if _original_translate is None:
    _original_translate = _legacy.translate_sql_for_postgres
    setattr(
        _legacy,
        "_UNGUARDED_TRANSLATE_SQL_FOR_POSTGRES",
        _original_translate,
    )


def translate_sql_for_postgres(sql: str) -> str:
    """Translate supported SQLite SQL and reject unsupported PRAGMA statements."""

    validate_sqlite_compat_statement(sql)
    return _original_translate(sql)


setattr(_legacy, "_replace_qmark_placeholders", replace_qmark_placeholders)
setattr(
    _legacy,
    "_translate_sqlite_master_tables_query",
    translate_sqlite_master_tables_query,
)
setattr(_legacy, "translate_sql_for_postgres", translate_sql_for_postgres)

# Re-export the full historical surface, including private compatibility helpers
# used by existing tests and migrations. Dunder import metadata must remain owned
# by this facade so importlib.reload() continues to execute this file.
for _name, _value in vars(_legacy).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

# Ensure the facade's guarded translator remains the visible canonical function.
globals()["translate_sql_for_postgres"] = translate_sql_for_postgres


class _CoreFacadeModule(_types.ModuleType):
    """Mirror runtime monkeypatches into the unchanged implementation module."""

    def __getattr__(self, name: str):
        return getattr(_legacy, name)

    def __setattr__(self, name: str, value) -> None:
        if not name.startswith("__") and hasattr(_legacy, name):
            setattr(_legacy, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if not name.startswith("__") and hasattr(_legacy, name):
            delattr(_legacy, name)
        super().__delattr__(name)


_sys.modules[__name__].__class__ = _CoreFacadeModule
