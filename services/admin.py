from __future__ import annotations

import logging
import sqlite3

from config.settings import ADMIN_IDS


def _is_admin_by_db(user_id: int) -> bool:
    """Дополнительная проверка "админности" через БД.

    В проекте теперь есть роли и ограничения прав админов, которые настраиваются из админки.
    Поэтому, кроме статичного списка ADMIN_IDS в settings, считаем админом любого пользователя,
    у которого:
    - есть хотя бы одна роль в user_roles, или
    - есть записи в admin_permissions.

    Это позволяет добавлять админов/команду без перезапуска и правки конфига.
    """
    try:
        from services.roles import user_roles
        if user_roles(int(user_id)):
            return True
    except ImportError:
        logging.getLogger(__name__).exception("DB role check failed")
    except (sqlite3.Error, TypeError, ValueError):
        logging.getLogger(__name__).exception("DB role check failed")
    try:
        from services.admin_permissions import get_allowed_perms
        # None означает "ограничений нет" — но сам факт наличия записей нам важен.
        # get_allowed_perms() возвращает None, если записей нет; иначе set(...)
        perms = get_allowed_perms(int(user_id))
        if perms is not None:
            return True
    except ImportError:
        logging.getLogger(__name__).exception("DB perms check failed")
    except (sqlite3.Error, TypeError, ValueError):
        logging.getLogger(__name__).exception("DB perms check failed")
    return False

def is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    try:
        uid = int(user_id)
        if uid in set(ADMIN_IDS):
            return True
        return _is_admin_by_db(uid)
    except (TypeError, ValueError):
        logging.getLogger(__name__).exception("Bad admin id")
        return False
