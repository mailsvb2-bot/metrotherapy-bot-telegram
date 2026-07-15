from __future__ import annotations
"""Database package.

This project historically exposed DB helpers from a *module* ``services/db.py``.
We now also need a *package* ``services/db/`` to host the schema split:
``services/db/schema/*``.

Python resolves ``import services.db`` to the *package* when it exists, so we
must keep the old public API available from this package.

The canonical implementation of the connection helpers lives in
:mod:`services.db.core`. Public convenience helpers that materialize query
results live here so callers never receive a cursor backed by a closed
connection.
"""

import time
from typing import Any, Sequence


# Re-export the public connection helpers expected across the codebase.
from services.db.core import (
    DB_PATH,
    PROJECT_ROOT,
    db,
    get_connection,
    get_db,
    get_db_ro,
    tx,
    write,
)


def execute(
    sql: str,
    params: Sequence[Any] = (),
    *,
    fetchone: bool = False,
    fetchall: bool = False,
):
    """Execute one statement and return a value that survives connection close.

    The old package re-exported ``services.db.core.execute`` which returned a raw
    cursor from inside ``with db()``. That cursor was already detached from a
    closed/returned connection, and legacy callers also passed ``fetchone=True``
    even though the core helper did not accept it. Materialize reads before the
    context exits and return rowcount for writes.
    """

    if fetchone and fetchall:
        raise ValueError("execute accepts only one of fetchone/fetchall")
    with db() as conn:
        cursor = conn.execute(sql, tuple(params))
        if fetchone:
            return cursor.fetchone()
        if fetchall:
            return cursor.fetchall()
        if getattr(cursor, "description", None) is not None:
            return cursor.fetchall()
        try:
            return int(getattr(cursor, "rowcount", 0) or 0)
        except (TypeError, ValueError):
            return 0


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
from services.db import schema

# Make the package itself callable for legacy ``from services import db``
# compatibility without shadowing the ``services.db`` package namespace.
# This preserves canonical submodule imports such as ``services.db.core``.
import sys as _sys
import types as _types


class _CallableDbPackage(_types.ModuleType):
    def __call__(self, *args, **kwargs):
        return db(*args, **kwargs)


_sys.modules[__name__].__class__ = _CallableDbPackage
