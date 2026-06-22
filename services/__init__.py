"""Services public API.

Canonical rule:
- ``services.db`` remains the database package, so imports such as
  ``import services.db.core`` cannot be shadowed by a helper function.
- legacy ``from services import db`` still returns a callable object: the
  database package itself delegates calls to ``services.db.core.db``.

This avoids the dangerous package/function name split-brain while preserving
external compatibility.
"""

from __future__ import annotations

import importlib as _importlib

_db_package = _importlib.import_module("services.db")

# Keep the package object on ``services.db``.  The package is callable because
# services.db.__init__ installs a ModuleType subclass that delegates __call__ to
# the canonical DB helper.
db = _db_package
get_db = _db_package.get_db
tx = _db_package.tx

from services.schema import init_db
from services.store import store
from services.subscription import get_scope, has_access, is_active
from services.access import get_subscription_scope, grant_subscription, has_active_subscription

__all__ = [
    "db",
    "get_db",
    "tx",
    "init_db",
    "store",
    "has_access",
    "is_active",
    "get_scope",
    "has_active_subscription",
    "get_subscription_scope",
    "grant_subscription",
]
