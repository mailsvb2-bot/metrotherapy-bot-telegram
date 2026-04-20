from __future__ import annotations

"""Мини-хранилище временных действий пользователя.

Используется для маленьких flows, где следующий шаг ожидается текстом (Message),
например ввод города или времени. Хранилище in-memory (без БД), поэтому если бот
перезапустился — пользователь просто повторит шаг (это ок).

Важно: здесь НЕ должно быть 'тихих' ошибок. Любая проблема логируется.
"""

from dataclasses import dataclass
from time import time
from typing import Any, Dict, Optional
import logging

log = logging.getLogger(__name__)


@dataclass
class Pending:
    user_id: int
    kind: str
    data: dict[str, Any] | None
    created_ts: float
    ttl_sec: int


_PENDING: Dict[int, Pending] = {}


def set_pending(user_id: int, kind: str, data: dict[str, Any] | None = None, *, ttl_sec: int = 600) -> None:
    """Сохранить ожидание следующего шага."""
    try:
        _PENDING[int(user_id)] = Pending(
            user_id=int(user_id),
            kind=str(kind),
            data=data or {},
            created_ts=time(),
            ttl_sec=int(ttl_sec),
        )
    except (TypeError, ValueError):
        log.exception("pending.set_pending failed")
        # не бросаем дальше — это UX-хранилище, но проблемы должны быть видны


def peek_pending(user_id: int) -> Pending | None:
    """Посмотреть pending, не извлекая."""
    try:
        p = _PENDING.get(int(user_id))
        if not p:
            return None
        if (time() - p.created_ts) > int(p.ttl_sec):
            _PENDING.pop(int(user_id), None)
            return None
        return p
    except (TypeError, ValueError, KeyError):
        log.exception("pending.peek_pending failed")
        return None


def pop_pending(user_id: int) -> Pending | None:
    """Получить и удалить pending."""
    try:
        p = peek_pending(int(user_id))
        _PENDING.pop(int(user_id), None)
        return p
    except (TypeError, ValueError, KeyError):
        log.exception("pending.pop_pending failed")
        return None


def clear_pending(user_id: int) -> None:
    try:
        _PENDING.pop(int(user_id), None)
    except (TypeError, ValueError, KeyError):
        log.exception("pending.clear_pending failed")
