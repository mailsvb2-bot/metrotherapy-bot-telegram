from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from core.time_utils import utc_now

from services.db import db

MAX_PRICE_RUB = 1_000_000

log = logging.getLogger(__name__)


def _table_missing(e: Exception) -> bool:
    msg = str(e).lower()
    return "no such table" in msg or "does not exist" in msg
def set_plan_price(code: str, price: int, conn=None, *, changed_by: int | None = None) -> bool:
    """Изменить цену плана в БД (в рублях, целое число).

    Возвращает True, если хотя бы одна строка обновлена.
    """

    # Валидация цены, чтобы админкой нельзя было записать мусор/0/минус.
    try:
        price = int(price)
    except (TypeError, ValueError):
        log.warning("Некорректная цена: %r (code=%s)", price, code)
        return False
    if price <= 0 or price > MAX_PRICE_RUB:
        log.warning("Цена вне допустимого диапазона: %s (code=%s)", price, code)
        return False

    now = utc_now().replace(microsecond=0).isoformat()

    def _set(_conn) -> bool:
        old = None
        try:
            row = _conn.execute("SELECT price FROM plans WHERE code=?", (str(code),)).fetchone()
            if row is not None:
                old = int(row[0] if not hasattr(row, "keys") else row["price"])  # type: ignore[index]
        except sqlite3.Error:
            log.exception("Не удалось прочитать старую цену (sqlite)")
        except (KeyError, TypeError, ValueError):
            log.exception("Не удалось прочитать старую цену (bad row)")

        cur = _conn.execute(
            "UPDATE plans SET price=?, updated_at=? WHERE code=?",
            (int(price), now, str(code)),
        )

        # В sqlite3 cursor.rowcount может быть 0, если значение не изменилось.
        # Для админки это не должно выглядеть как "тариф не найден".
        rowcount = getattr(cur, "rowcount", 0)
        updated = bool(rowcount and rowcount > 0)

        # Если строка существует, но цена уже такая же — считаем как успешно применено,
        # но историю цен не пишем (old==new).
        if (not updated) and old is not None and int(old) == int(price):
            updated = True

        if updated and (old is None or int(old) != int(price)):
            # История цен — опциональная таблица. Схема создаётся в init_db.
            try:
                _conn.execute(
                    "INSERT INTO plan_price_history(plan_code, old_price, new_price, changed_at_utc, changed_by) VALUES(?,?,?,?,?)",
                    (str(code), old, int(price), now, int(changed_by) if changed_by is not None else None),
                )
            except sqlite3.Error as e:
                if _table_missing(e):
                    return updated
                log.exception("Не удалось записать историю цен")
        return updated

    if conn is not None:
        return _set(conn)
    with db() as c:
        return _set(c)
def set_plan_price_by_code(code: str, price: int, conn=None, *, changed_by: int | None = None) -> bool:
    return set_plan_price(code=code, price=price, conn=conn, changed_by=changed_by)
def set_plan_price_by_title(title: str, price: int, conn=None, *, changed_by: int | None = None) -> bool:
    """Установить цену тарифа по русскому названию."""

    from services.plans import get_plans

    import difflib

    target = _norm_title(title)
    plans = get_plans(include_inactive=True)

    # 1) точное совпадение (после нормализации)
    for p in plans:
        if _norm_title(str(p.get("title") or "")) == target:
            code = str(p.get("code") or "")
            if not code:
                break
            return set_plan_price(code=code, price=price, conn=conn, changed_by=changed_by)

    # 2) частичное совпадение (подстрока)
    candidates: list[tuple[float, str]] = []
    for p in plans:
        t = _norm_title(str(p.get("title") or ""))
        code = str(p.get("code") or "")
        if not t or not code:
            continue
        if target and (target in t or t in target):
            candidates.append((0.90, code))

    # 3) похожее совпадение (SequenceMatcher)
    if not candidates:
        for p in plans:
            t = _norm_title(str(p.get("title") or ""))
            code = str(p.get("code") or "")
            if not t or not code:
                continue
            score = difflib.SequenceMatcher(a=target, b=t).ratio()
            candidates.append((score, code))

    if not candidates:
        raise ValueError("Тарифов нет")

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_code = candidates[0]
    if best_score < 0.78:
        raise ValueError("Тариф с таким названием не найден")

    return set_plan_price(code=best_code, price=price, conn=conn, changed_by=changed_by)
def set_plan_prices_by_titles(
    updates: list[tuple[str, int]],
    *,
    changed_by: int | None = None,
) -> tuple[int, list[str]]:
    """Пакетное применение цен по русским названиям.

    Заметно ускоряет админский ввод, потому что:
    - планы читаются из БД один раз
    - сопоставление названий делается в памяти
    - все UPDATE выполняются в одном соединении

    Возвращает: (applied_count, not_found_titles)
    """

    from services.plans import get_plans

    plans = get_plans(include_inactive=True)
    # Индекс по нормализованному названию → code
    title_to_code: dict[str, str] = {}
    raw_titles: dict[str, str] = {}
    for p in plans:
        raw = str(p.get("title") or "").strip()
        code = str(p.get("code") or "").strip()
        if not raw or not code:
            continue
        title_to_code[_norm_title(raw)] = code
        raw_titles[_norm_title(raw)] = raw

    applied = 0
    not_found: list[str] = []

    with db() as conn:
        for raw_title, price in updates:
            norm = _norm_title(raw_title)

            # 1) точное совпадение
            code = title_to_code.get(norm)

            # 2) подстрока
            if not code:
                for t_norm, c in title_to_code.items():
                    if norm and (norm in t_norm or t_norm in norm):
                        code = c
                        break

            # 3) похожесть
            if not code:
                import difflib

                best_score = 0.0
                best_code = ""
                for t_norm, c in title_to_code.items():
                    score = difflib.SequenceMatcher(a=norm, b=t_norm).ratio()
                    if score > best_score:
                        best_score, best_code = score, c
                if best_score >= 0.78:
                    code = best_code

            if not code:
                not_found.append(raw_title)
                continue

            ok = set_plan_price(code=code, price=int(price), conn=conn, changed_by=changed_by)
            if ok:
                applied += 1
            else:
                # Теоретически возможно, если code некорректен
                not_found.append(raw_title)

    return applied, not_found
def set_plan_prices_by_titles_verbose(
    updates: list[tuple[str, int]],
    *,
    changed_by: int | None = None,
) -> tuple[int, list[str], list[dict]]:
    """Пакетное применение цен по русским названиям с подробным отчётом.

    Возвращает:
      applied_count: сколько строк было успешно сопоставлено и применено
      not_found_titles: какие заголовки не удалось сопоставить
      details: список словарей по каждой строке:
        {"input": <что ввели>, "title": <как в БД>, "code": <code>,
         "old": <старая цена>, "new": <новая цена>, "changed": bool}
    """

    from services.plans import get_plans

    plans = get_plans(include_inactive=True)
    title_to_code: dict[str, str] = {}
    norm_to_raw: dict[str, str] = {}
    id_to_code: dict[int, str] = {}
    code_set: set[str] = set()
    for p in plans:
        raw = str(p.get("title") or "").strip()
        code = str(p.get("code") or "").strip()
        pid = int(p.get("id") or 0)
        if not raw or not code:
            continue
        n = _norm_title(raw)
        title_to_code[n] = code
        norm_to_raw[n] = raw
        if pid:
            id_to_code[pid] = code
        code_set.add(code)

    import difflib

    applied = 0
    not_found: list[str] = []
    details: list[dict] = []

    with db() as conn:
        for raw_title, price in updates:
            raw_title = str(raw_title or "").strip()
            norm = _norm_title(raw_title)

            # 0) Админ может вводить код тарифа напрямую (например: both_5=4900)
            code = raw_title if raw_title in code_set else ""

            # 0.1) Или ID тарифа (например: 12=4900)
            if not code and raw_title.isdigit():
                code = id_to_code.get(int(raw_title), "")

            # 1) Точное совпадение по названию
            if not code:
                code = title_to_code.get(norm)
            matched_norm = norm if code else ""

            if not code:
                for t_norm, c in title_to_code.items():
                    if norm and (norm in t_norm or t_norm in norm):
                        code = c
                        matched_norm = t_norm
                        break

            if not code:
                best_score = 0.0
                best_code = ""
                best_norm = ""
                for t_norm, c in title_to_code.items():
                    score = difflib.SequenceMatcher(a=norm, b=t_norm).ratio()
                    if score > best_score:
                        best_score, best_code, best_norm = score, c, t_norm
                if best_score >= 0.78:
                    code = best_code
                    matched_norm = best_norm

            if not code:
                not_found.append(raw_title)
                details.append({"input": raw_title, "title": "", "code": "", "old": None, "new": int(price), "changed": False})
                continue

            # Читаем старую цену (в рублях)
            old = None
            try:
                row = conn.execute("SELECT price FROM plans WHERE code=?", (str(code),)).fetchone()
                if row is not None:
                    old = int(row[0] if not hasattr(row, "keys") else row["price"])  # type: ignore[index]
            except sqlite3.Error:
                log.exception("Не удалось прочитать старую цену для code=%s (input=%s)", code, raw_title)
            except (KeyError, TypeError, ValueError):
                log.exception("Не удалось прочитать старую цену (bad row) для code=%s (input=%s)", code, raw_title)

            ok = set_plan_price(code=code, price=int(price), conn=conn, changed_by=changed_by)
            if ok:
                applied += 1

            title_raw = norm_to_raw.get(matched_norm) or raw_title
            changed = (old is None) or (int(old) != int(price))
            details.append({
                "input": raw_title,
                "title": title_raw,
                "code": code,
                "old": old,
                "new": int(price),
                "changed": bool(changed),
            })

    return applied, not_found, details
