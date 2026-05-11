from __future__ import annotations

import logging
import sqlite3

from config.settings import ADMIN_IDS


# Roles that are allowed to enter the staff/admin control surface.
# This is intentionally broader than ROLE_ADMIN because the project supports
# delegated staff roles (marketing/support/etc.) with scoped permissions.
_STAFF_ROLE_NAMES = {
    "admin",
    "support",
    "marketing",
    "copywriter",
    "developer",
    "targetologist",
    "analyst",
}


def _uid(user_id: int | None) -> int | None:
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        logging.getLogger(__name__).exception("Bad admin id")
        return None


def is_superadmin(user_id: int | None) -> bool:
    """Return whether user_id is one of immutable env-configured superadmins."""
    uid = _uid(user_id)
    if uid is None:
        return False
    return uid in set(int(x) for x in (ADMIN_IDS or []))


def _roles_for(user_id: int) -> set[str]:
    try:
        from services.roles import user_roles

        return {str(role).strip().lower() for role in (user_roles(int(user_id)) or set()) if str(role).strip()}
    except ImportError:
        logging.getLogger(__name__).exception("DB role check failed")
    except (sqlite3.Error, TypeError, ValueError):
        logging.getLogger(__name__).exception("DB role check failed")
    return set()


def _allowed_permissions_for(user_id: int) -> set[str] | None:
    try:
        from services.admin_permissions import get_allowed_perms

        perms = get_allowed_perms(int(user_id))
        if perms is None:
            return None
        return {str(perm) for perm in perms if str(perm).strip()}
    except ImportError:
        logging.getLogger(__name__).exception("DB perms check failed")
    except (sqlite3.Error, TypeError, ValueError):
        logging.getLogger(__name__).exception("DB perms check failed")
    return None


def has_any_allowed_permission(user_id: int | None) -> bool:
    """True only when explicit permission rows contain at least one allowed perm.

    Critical security detail: an empty set means "explicitly restricted to no
    permissions" and must NOT grant generic admin access.
    """
    uid = _uid(user_id)
    if uid is None:
        return False
    perms = _allowed_permissions_for(uid)
    return bool(perms)


def staff_roles(user_id: int | None) -> set[str]:
    """Return effective staff roles granted through the DB role table."""
    uid = _uid(user_id)
    if uid is None:
        return set()
    return _roles_for(uid) & _STAFF_ROLE_NAMES


def is_platform_admin(user_id: int | None) -> bool:
    """High-trust admin check for sensitive global commands.

    Use this for commands that expose whole-project data or mutate global state.
    Scoped staff roles are intentionally not enough here.
    """
    uid = _uid(user_id)
    if uid is None:
        return False
    if is_superadmin(uid):
        return True
    return "admin" in _roles_for(uid)


def is_staff(user_id: int | None) -> bool:
    """Return whether user can enter the delegated staff/admin surface."""
    uid = _uid(user_id)
    if uid is None:
        return False
    if is_superadmin(uid):
        return True
    if staff_roles(uid):
        return True
    return has_any_allowed_permission(uid)


def is_admin(user_id: int | None) -> bool:
    """Backward-compatible admin-gate API.

    Historically this function was used both for superadmins and delegated staff
    control-plane access. It now keeps that compatibility while fixing the P0
    bug where "permission rows exist, but all are denied" still granted access.
    """
    return is_staff(user_id)
