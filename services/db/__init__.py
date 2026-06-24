from __future__ import annotations
"""Database package.

This project historically exposed DB helpers from a *module* ``services/db.py``.
We now also need a *package* ``services/db/`` to host the schema split:
``services/db/schema/*``.

Python resolves ``import services.db`` to the *package* when it exists, so we
must keep the old public API available from this package.

The canonical implementation of the DB helpers lives in :mod:`services.db.core`.
"""

import time
from typing import Any


# Re-export the public DB helpers expected across the codebase.
from services.db.core import (  # noqa: F401
    DB_PATH,
    PROJECT_ROOT,
    db,
    execute,
    get_connection,
    get_db,
    get_db_ro,
    tx,
    write,
)


def _delivery_key(*parts: Any) -> str:
    """Build the canonical idempotency key used by delivery/job callers.

    Backward compatibility is intentional:
    - legacy code used ``mark_delivery_once(user_id, key)``;
    - newer scheduler/audio code uses semantic parts such as
      ``mark_delivery_once(user_id, kind, stage, scheduled_at)``.

    The package public API owns that compatibility so runtime callers do not
    drift into split implementations or per-call ad-hoc joins.
    """
    cleaned = [str(p).strip() for p in parts if str(p).strip()]
    if not cleaned:
        raise ValueError("delivery idempotency key must not be empty")
    return ":".join(cleaned)


def _is_deferred_engine_job_marker(*parts: Any) -> bool:
    """Engine job delivery markers are written only after mark_done().

    Engine.tick used to call mark_delivery_once('job', job_type, job_key) before
    executing the side effect. A crash between that marker and the effect could
    make the next tick close the job as already delivered. Keep the public call
    as a no-op compatibility guard; services.jobs.mark_done() writes the real
    delivered marker only for successful job completion.
    """
    return len(parts) >= 3 and str(parts[0]).strip() == "job"


def was_delivered(user_id: int, *key_parts: Any) -> bool:
    key = _delivery_key(*key_parts)
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
            (int(user_id), key),
        ).fetchone()
        return bool(row)


def mark_delivery_once(user_id: int, *key_parts: Any) -> bool:
    key = _delivery_key(*key_parts)
    if _is_deferred_engine_job_marker(*key_parts):
        return True

    created_at = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO idempotency(user_id, key, created_at) VALUES(?,?,?)",
            (int(user_id), key, created_at),
        )
        row = conn.execute("SELECT changes() AS c").fetchone()
        return int(row["c"] if hasattr(row, "keys") else row[0]) == 1


def unmark_delivery(user_id: int, *key_parts: Any) -> None:
    key = _delivery_key(*key_parts)
    with db() as conn:
        conn.execute("DELETE FROM idempotency WHERE user_id=? AND key=?", (int(user_id), key))


# Schema split package (DDL-only)
from services.db import schema  # noqa: F401,E402

# Make the package itself callable for legacy ``from services import db``
# compatibility without shadowing the ``services.db`` package namespace.
# This preserves canonical submodule imports such as ``services.db.core``.
import sys as _sys
import types as _types


class _CallableDbPackage(_types.ModuleType):
    def __call__(self, *args, **kwargs):
        return db(*args, **kwargs)


_sys.modules[__name__].__class__ = _CallableDbPackage
