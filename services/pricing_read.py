from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from core.time_utils import utc_now

from services.db import db

MAX_PRICE_RUB = 1_000_000

log = logging.getLogger(__name__)


def _norm_title(s: str) -> str:
    """Нормализация названий тарифов.

    Нужна, чтобы админ мог вводить по-русски, не переживая про разные дефисы
    (—/–/-), лишние пробелы и регистр.
    """

    s = (s or "").strip().lower()
    # разные типы дефисов → обычный
    s = s.replace("—", "-").replace("–", "-")
    # убираем повторные пробелы вокруг дефисов
    s = re.sub(r"\s*-\s*", " - ", s)
    s = re.sub(r"\s+", " ", s)
    return s
def suggest_plan_titles(input_title: str, plans: list[dict] | None = None, limit: int = 5) -> list[str]:
    """Подсказки по названиям тарифов для админского ввода."""

    import difflib
    from services.plans import get_plans

    target = _norm_title(input_title)
    _plans = plans if plans is not None else get_plans(include_inactive=True)
    titles: list[str] = []
    scored: list[tuple[float, str]] = []
    for p in _plans:
        raw = str(p.get("title") or "").strip()
        if not raw:
            continue
        t = _norm_title(raw)
        score = difflib.SequenceMatcher(a=target, b=t).ratio()
        scored.append((score, raw))
    scored.sort(key=lambda x: x[0], reverse=True)
    for score, raw in scored[:limit]:
        if raw not in titles:
            titles.append(raw)
    return titles
def read_plans(conn=None) -> dict[str, int]:
    """Источник истины для цен: таблица plans.

    Возвращает словарь {code: price_rub} только для активных планов.
    """

    def _read(_conn) -> dict[str, int]:
        rows = _conn.execute("SELECT code, price FROM plans WHERE is_active=1").fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    if conn is not None:
        return _read(conn)
    with db() as c:
        return _read(c)
