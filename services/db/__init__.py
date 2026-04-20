from __future__ import annotations
"""Database package.

This project historically exposed DB helpers from a *module* ``services/db.py``.
We now also need a *package* ``services/db/`` to host the schema split:
``services/db/schema/*``.

Python resolves ``import services.db`` to the *package* when it exists, so we
must keep the old public API available from this package.

The canonical implementation of the DB helpers lives in :mod:`services.db.core`.
"""


# Re-export the public DB helpers expected across the codebase.
from services.db.core import (  # noqa: F401
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

# Schema split package (DDL-only)
from services.db import schema  # noqa: F401
