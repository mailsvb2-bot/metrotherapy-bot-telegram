from __future__ import annotations
"""Backward-compatible public database package.

Write-capable runtime helpers are implemented in :mod:`services.db.core`.
The public read-only context is deliberately provided by
:mod:`services.db.read_only`, which enables and verifies database-level
read-only mode before yielding a connection wrapper.

The package also installs the fail-closed PostgreSQL compatibility guards before
any public DB helper is exported. This keeps legacy SQLite-flavoured callers
compatible without allowing unsupported PRAGMA statements or ambiguous qmark
rewrites to reach the PostgreSQL driver.
"""

from services.db import core as _core
from services.db.sql_compat_guard import install_sql_compat_guards

install_sql_compat_guards(_core)

from services.db.core import (  # noqa: E402
    DB_PATH,
    PROJECT_ROOT,
    db,
    execute,
    get_connection,
    get_db,
    mark_delivery_once,
    tx,
    unmark_delivery,
    was_delivered,
    write,
)
from services.db.read_only import get_db_ro  # noqa: E402

from services.db import schema  # noqa: E402

import sys as _sys
import types as _types


class _CallableDbPackage(_types.ModuleType):
    def __call__(self, *args, **kwargs):
        return db(*args, **kwargs)


_sys.modules[__name__].__class__ = _CallableDbPackage
