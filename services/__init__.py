"""Lazy public API for :mod:`services`.

Canonical rule:
- importing a leaf module such as ``services.practice_token_contract`` must not
  eagerly initialize the database, schema, stores, or subscription stack;
- legacy ``from services import db`` remains supported and returns the callable
  ``services.db`` package;
- the remaining historical convenience exports are resolved only when actually
  requested.

Keeping package import side-effect-light is especially important for production
operator tools and provider probes that intentionally import one narrow service
without bootstrapping the whole application runtime.
"""

from __future__ import annotations

import importlib as _importlib
from typing import Any as _Any

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


_DB_EXPORTS = frozenset({"db", "get_db", "tx"})
_SUBSCRIPTION_EXPORTS = frozenset({"has_access", "is_active", "get_scope"})
_ACCESS_EXPORTS = frozenset(
    {"has_active_subscription", "get_subscription_scope", "grant_subscription"}
)


def _cache(name: str, value: _Any) -> _Any:
    globals()[name] = value
    return value


def __getattr__(name: str) -> _Any:
    if name in _DB_EXPORTS:
        package = _importlib.import_module("services.db")
        if name == "db":
            # services.db is deliberately a callable ModuleType for backwards
            # compatibility; returning the package keeps package/function
            # identity canonical instead of creating a split-brain alias.
            return _cache(name, package)
        return _cache(name, getattr(package, name))

    if name == "init_db":
        module = _importlib.import_module("services.schema")
        return _cache(name, module.init_db)

    if name == "store":
        module = _importlib.import_module("services.store")
        return _cache(name, module.store)

    if name in _SUBSCRIPTION_EXPORTS:
        module = _importlib.import_module("services.subscription")
        return _cache(name, getattr(module, name))

    if name in _ACCESS_EXPORTS:
        module = _importlib.import_module("services.access")
        return _cache(name, getattr(module, name))

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
