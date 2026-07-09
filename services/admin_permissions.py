from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from core.time_utils import utc_now
from typing import Iterable

from services.db import db
from core.callbacks import ADMIN_TARIFFS


# Права храним как строки. Чтобы не усложнять UX —
# используем callback_data как идентификатор права.
# Супер-админ всегда имеет доступ ко всему.


def get_allowed_perms(admin_id: int) -> set[str] | None:
    """Возвращает set разрешённых perm или None, если ограничений не настроено.

    Логика:
    - если для admin_id нет записей в admin_permissions -> None (не ограничиваем)
    - если записи есть -> возвращаем только allowed=1
    """
    admin_id = int(admin_id)
    with db() as conn:
        rows = conn.execute(
            "SELECT perm, allowed FROM admin_permissions WHERE admin_id=?",
            (admin_id,),
        ).fetchall()
    if not rows:
        return None
    allowed = {str(r[0] if not hasattr(r, 'keys') else r['perm']) for r in rows if int(r[1] if not hasattr(r, 'keys') else r['allowed']) == 1}
    return allowed


def set_perm(admin_id: int, perm: str, allowed: bool, *, updated_by: int | None = None) -> None:
    admin_id = int(admin_id)
    perm = str(perm)
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        conn.execute(
            "INSERT INTO admin_permissions(admin_id, perm, allowed, updated_at_utc, updated_by) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(admin_id, perm) DO UPDATE SET allowed=excluded.allowed, updated_at_utc=excluded.updated_at_utc, updated_by=excluded.updated_by",
            (admin_id, perm, 1 if allowed else 0, now, int(updated_by) if updated_by is not None else None),
        )
        conn.commit()


def toggle_perm(admin_id: int, perm: str, *, updated_by: int | None = None) -> bool:
    """Переключает perm и возвращает новое значение allowed."""
    admin_id = int(admin_id)
    perm = str(perm)
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        row = conn.execute(
            "SELECT allowed FROM admin_permissions WHERE admin_id=? AND perm=?",
            (admin_id, perm),
        ).fetchone()
        current = None
        if row is not None:
            current = int(row[0] if not hasattr(row, 'keys') else row['allowed'])
        new_allowed = 0 if current == 1 else 1
        conn.execute(
            "INSERT INTO admin_permissions(admin_id, perm, allowed, updated_at_utc, updated_by) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(admin_id, perm) DO UPDATE SET allowed=excluded.allowed, updated_at_utc=excluded.updated_at_utc, updated_by=excluded.updated_by",
            (admin_id, perm, new_allowed, now, int(updated_by) if updated_by is not None else None),
        )
        conn.commit()
    return bool(new_allowed)


def list_admin_ids(limit: int = 200) -> list[int]:
    """Список всех пользователей, у которых есть роли (кандидаты в админы)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM user_roles ORDER BY user_id LIMIT ?",
            (int(limit),),
        ).fetchall()
    out: list[int] = []
    for r in rows or []:
        out.append(int(r[0] if not hasattr(r, 'keys') else r['user_id']))
    return out


@dataclass(frozen=True)
class PermItem:
    perm: str
    title: str


# Набор прав, который показывает супер-админ в UI.
# При добавлении новых кнопок — просто добавляйте сюда.
PERMS: list[PermItem] = [
    PermItem("admin:demo:brief", "📊 Демо (кратко)"),
    PermItem("admin:demo:full", "📈 Демо (подробно)"),
    PermItem("admin:users:today", "👥 Пользователи сегодня"),
    PermItem("admin:user:card", "🔎 Карточка пользователя"),
    PermItem("admin:behavior", "🧠 Поведение"),
    PermItem("admin:growth:autopilot", "🤖 Growth Autopilot"),
    PermItem("admin:funnel", "📉 Воронка"),
    PermItem("admin:money:today", "💰 Деньги и клиенты"),
    PermItem("admin:conversion", "💰 Конверсия"),
    PermItem("admin:segments", "🧲 Сегменты"),
    PermItem("admin:ab", "🧪 Тесты офферов"),
    PermItem("admin:copy:menu", "🤖 ИИ-копирайтер"),
    PermItem("admin:ai:prices", "🤖 ИИ-цены"),
    PermItem("admin:giftshare", "🎁 Подарки и рекомендации"),
    PermItem("admin:funnel2", "🧲 Воронка 2.0"),
    PermItem("admin:retention", "🧩 Удержание"),
    PermItem("admin:state:last", "🧾 Мои состояния (10)"),
    # Тарифы — обычно только супер-админ, но если вдруг нужно делегировать.
    PermItem(ADMIN_TARIFFS, "💳 Тарифы"),
]
