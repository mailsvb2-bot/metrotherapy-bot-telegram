"""Services public API.

Цель: единый контракт импортов по проекту.
Снаружи пакета можно импортировать коротко:

    from services import db, init_db, store, has_access

Внутри пакета используем ТОЛЬКО абсолютные импорты `from services.xxx import ...`.
"""

from services.db import db, get_db, tx
from services.schema import init_db
from services.store import store
from services.subscription import has_access, is_active, get_scope
from services.access import has_active_subscription, get_subscription_scope, grant_subscription

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
