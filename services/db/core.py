from __future__ import annotations

"""Canonical database compatibility facade.

The established database implementation lives in :mod:`services.db.core_legacy`
unchanged. This facade installs narrow fail-closed execution guards while
preserving the complete historical translation API and mutable module surface.
Keeping the guards in this importable facade makes them deterministic across
normal imports and ``importlib.reload`` without rewriting working DB behavior.
"""

import sys as _sys
import types as _types
from typing import Any, Callable, Sequence

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

# Preserve the historical direct helper contract. Some migration and regression
# code intentionally inspects translations without executing them. Fail-closed
# behavior belongs to the actual cursor boundary below, not this pure helper.
def translate_sql_for_postgres(sql: str) -> str:
    return _original_translate(sql)


setattr(_legacy, "_replace_qmark_placeholders", replace_qmark_placeholders)
setattr(
    _legacy,
    "_translate_sqlite_master_tables_query",
    translate_sqlite_master_tables_query,
)
setattr(_legacy, "translate_sql_for_postgres", translate_sql_for_postgres)

_cursor_type = _legacy.PostgresCompatCursor
_original_cursor_execute: Callable[..., Any] | None = getattr(
    _legacy,
    "_UNGUARDED_POSTGRES_CURSOR_EXECUTE",
    None,
)
if _original_cursor_execute is None:
    _original_cursor_execute = _cursor_type.execute
    setattr(
        _legacy,
        "_UNGUARDED_POSTGRES_CURSOR_EXECUTE",
        _original_cursor_execute,
    )

_original_cursor_executemany: Callable[..., Any] | None = getattr(
    _legacy,
    "_UNGUARDED_POSTGRES_CURSOR_EXECUTEMANY",
    None,
)
if _original_cursor_executemany is None:
    _original_cursor_executemany = _cursor_type.executemany
    setattr(
        _legacy,
        "_UNGUARDED_POSTGRES_CURSOR_EXECUTEMANY",
        _original_cursor_executemany,
    )


def _guarded_cursor_execute(
    self: Any,
    sql: str,
    params: Sequence[Any] = (),
) -> Any:
    validate_sqlite_compat_statement(sql)
    assert _original_cursor_execute is not None
    return _original_cursor_execute(self, sql, params)


def _guarded_cursor_executemany(
    self: Any,
    sql: str,
    seq_of_params: Any,
) -> Any:
    validate_sqlite_compat_statement(sql)
    assert _original_cursor_executemany is not None
    return _original_cursor_executemany(self, sql, seq_of_params)


_cursor_type.execute = _guarded_cursor_execute
_cursor_type.executemany = _guarded_cursor_executemany

# Re-export the full historical surface, including private compatibility helpers
# used by existing tests and migrations. Dunder import metadata must remain owned
# by this facade so importlib.reload() continues to execute this file.
for _name, _value in vars(_legacy).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

# Ensure guarded facade functions remain canonical after the re-export loop.
globals()["translate_sql_for_postgres"] = translate_sql_for_postgres
globals()["PostgresCompatCursor"] = _cursor_type


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
