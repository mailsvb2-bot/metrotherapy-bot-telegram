from __future__ import annotations
"""Backward-compatible public database package.

All runtime helpers are implemented once in :mod:`services.db.core` and
re-exported here. The package remains callable for legacy
``from services import db`` call sites.
"""

from services.db.core import (
    DB_PATH,
    PROJECT_ROOT,
    db,
    execute,
    get_connection,
    get_db,
    get_db_ro,
    mark_delivery_once,
    tx,
    unmark_delivery,
    was_delivered,
    write,
)

from services.db import schema

import sys as _sys
import types as _types


class _CallableDbPackage(_types.ModuleType):
    def __call__(self, *args, **kwargs):
        return db(*args, **kwargs)


_sys.modules[__name__].__class__ = _CallableDbPackage
