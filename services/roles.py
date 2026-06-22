from __future__ import annotations


from typing import Iterable

from services.db import db


ROLE_ADMIN = "admin"
ROLE_SUPPORT = "support"
ROLE_MARKETING = "marketing"

# Роли команды
ROLE_COPYWRITER = "copywriter"
ROLE_DEVELOPER = "developer"
ROLE_TARGETOLOGIST = "targetologist"
ROLE_ANALYST = "analyst"

ALL_ROLES = (
    ROLE_ADMIN,
    ROLE_SUPPORT,
    ROLE_MARKETING,
    ROLE_COPYWRITER,
    ROLE_DEVELOPER,
    ROLE_TARGETOLOGIST,
    ROLE_ANALYST,
)


def user_roles(user_id: int) -> set[str]:
    user_id = int(user_id)
    with db() as conn:
        rows = conn.execute(
            "SELECT role FROM user_roles WHERE user_id=?",
            (user_id,),
        ).fetchall()
    return {str(r["role"]) for r in (rows or [])}


def has_any_role(user_id: int, roles: Iterable[str]) -> bool:
    rs = user_roles(user_id)
    for r in roles:
        if r in rs:
            return True
    return False


def grant_role(user_id: int, role: str) -> None:
    user_id = int(user_id)
    role = str(role).strip().lower()
    if role not in ALL_ROLES:
        raise ValueError("Unknown role")
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_roles(user_id, role) VALUES (?,?)",
            (user_id, role),
        )
        conn.commit()


def revoke_role(user_id: int, role: str) -> None:
    user_id = int(user_id)
    role = str(role).strip().lower()
    with db() as conn:
        conn.execute(
            "DELETE FROM user_roles WHERE user_id=? AND role=?",
            (user_id, role),
        )
        conn.commit()


def list_role_holders(role: str, limit: int = 200) -> list[int]:
    role = str(role).strip().lower()
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id FROM user_roles WHERE role=? ORDER BY user_id LIMIT ?",
            (role, int(limit)),
        ).fetchall()
    return [int(r["user_id"]) for r in (rows or [])]
