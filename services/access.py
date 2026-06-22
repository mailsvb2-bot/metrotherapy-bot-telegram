from __future__ import annotations


"""Единый API для доступа/подписки.

Цель: вынести проверки подписки из handlers в services слой, не ломая текущие контракты.

Важно:
- services/subscription.py остаётся источником данных и совместимости.
- этот модуль — тонкая обёртка с более говорящими именами для handlers.
"""

from services import subscription


def has_active_subscription(user_id: int) -> bool:
    """Есть ли у пользователя активная подписка."""
    return subscription.is_active(int(user_id))


def get_subscription_scope(user_id: int) -> str | None:
    """Текущий scope подписки (morning/evening/both) или None."""
    return subscription.get_scope(int(user_id))


def has_access(user_id: int, required_scope: str = "both") -> bool:
    """Проверка доступа к аудио (с учётом scope и срока подписки)."""
    return subscription.has_access(int(user_id), required_scope)


def grant_subscription(user_id: int, scope: str, days: int):
    """Выдать/продлить подписку."""
    subscription.grant(int(user_id), scope, int(days))
