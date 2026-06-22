from __future__ import annotations
import logging
import sqlite3

import json
from json import JSONDecodeError
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

from core.time_utils import utcnow_iso

from services.db import db




def log_state(user_id: int, state: str, meta: dict[str, Any] | None = None) -> None:
    """Пишет диагностический лог состояния пользователя.

    Таблица создаётся/мигрирует через services/schema.py:init_db().
    Функция намеренно лёгкая: одна вставка, без сложной логики.
    """

    if not user_id:
        return

    state = (state or "").strip()[:64] or "unknown"
    payload = None
    if meta:
        try:
            payload = json.dumps(meta, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = None

    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO user_state_log(user_id, state, ts, meta) VALUES(?,?,?,?)",
                (int(user_id), state, utcnow_iso(), payload),
            )
    except sqlite3.Error:
        # логирование не должно ронять бота, но ошибки нам нужны для диагностики
        logging.getLogger(__name__).exception("Failed to insert user_state_log")
        return


def fetch_last(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """Возвращает последние записи state-log по пользователю (для админ-диагностики).

    Формат элементов: {"ts": str, "state": str, "meta": dict|str|None}
    """

    if not user_id:
        return []

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))

    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT ts, state, meta FROM user_state_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (int(user_id), int(limit)),
            ).fetchall()
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading user_state_log")
        return []

    out: list[dict[str, Any]] = []
    for ts, state, meta in rows:
        parsed: Any = None
        if meta:
            try:
                parsed = json.loads(meta)
            except JSONDecodeError:
                logging.getLogger(__name__).debug("Failed to parse state_log meta JSON", exc_info=True)
                parsed = meta
        out.append({"ts": ts, "state": state, "meta": parsed})
    return out


def activity_spans(user_ids: list[int], start_ts: str | None = None, end_ts: str | None = None) -> dict[int, dict[str, Any]]:
    """Возвращает активность по user_state_log для набора пользователей.

    Результат:
      {user_id: {"first_ts": str, "last_ts": str, "events": int, "span_sec": int}}
    
    Примечание: ts хранится в UTC ISO (timezone-aware). Сравнения строк работают,
    если start_ts/end_ts в том же ISO-формате.
    """

    if not user_ids:
        return {}

    ids = [int(x) for x in user_ids if x]
    if not ids:
        return {}

    where = "user_id IN (%s)" % ",".join(["?"] * len(ids))
    params: list[Any] = list(ids)

    if start_ts:
        where += " AND ts >= ?"
        params.append(start_ts)
    if end_ts:
        where += " AND ts < ?"
        params.append(end_ts)

    q = f"SELECT user_id, MIN(ts) AS a, MAX(ts) AS b, COUNT(*) AS c FROM user_state_log WHERE {where} GROUP BY user_id"

    try:
        with db() as conn:
            rows = conn.execute(q, params).fetchall()
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while aggregating user_state_log")
        return {}

    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        uid = int(r["user_id"]) if isinstance(r, dict) or hasattr(r, "keys") else int(r[0])
        a = r["a"] if isinstance(r, dict) or hasattr(r, "keys") else r[1]
        b = r["b"] if isinstance(r, dict) or hasattr(r, "keys") else r[2]
        c = int(r["c"] if isinstance(r, dict) or hasattr(r, "keys") else r[3])
        span = 0
        try:
            span = int(max(0, (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()))
        except (ValueError, TypeError):
            logging.getLogger(__name__).debug("Failed to compute activity span", exc_info=True)
            span = 0
        out[uid] = {"first_ts": a, "last_ts": b, "events": c, "span_sec": span}
    return out


def recent_hour_local(user_id: int, tz_name: str) -> int | None:
    """Возвращает час (0..23) последней активности пользователя в локальном TZ.

    Используется для лёгкой персонализации текста (утро/день/вечер/ночь).
    Если данных нет — возвращает None.
    """

    if not user_id:
        return None

    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    try:
        with db() as conn:
            row = conn.execute(
                "SELECT ts FROM user_state_log WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (int(user_id),),
            ).fetchone()
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading last activity")
        return None

    if not row:
        return None
    ts = row[0] if not isinstance(row, dict) and not hasattr(row, "keys") else row["ts"]
    try:
        dt = datetime.fromisoformat(ts).astimezone(tz)
        return int(dt.hour)
    except (ValueError, TypeError):
        logging.getLogger(__name__).exception("Failed to parse activity timestamp")
        return None


def first_hour_today_local(user_id: int, tz_name: str) -> int | None:
    """Возвращает час (0..23) первой активности пользователя за текущий локальный день.

    Идея: берём границы текущего дня в локальной TZ, переводим в UTC и
    выбираем MIN(ts) в этом диапазоне.

    Если пользователь сегодня ещё не проявлял активности — None.
    """

    if not user_id:
        return None

    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    now_local = datetime.now(tz)
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_local = day_start_local.replace(hour=23, minute=59, second=59)

    start_utc = day_start_local.astimezone(ZoneInfo("UTC")).replace(microsecond=0).isoformat()
    end_utc = (day_end_local.astimezone(ZoneInfo("UTC"))
               .replace(microsecond=0)
               .isoformat())

    try:
        with db() as conn:
            row = conn.execute(
                "SELECT MIN(ts) AS ts FROM user_state_log WHERE user_id=? AND ts>=? AND ts<=?",
                (int(user_id), start_utc, end_utc),
            ).fetchone()
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading first activity today")
        return None

    if not row:
        return None

    # sqlite3.Row поддерживает .keys(), но не поддерживает .get()
    if hasattr(row, "keys"):
        ts = row["ts"] if ("ts" in row.keys()) else None
    elif isinstance(row, dict):
        ts = row.get("ts")
    else:
        ts = row[0]
    if not ts:
        return None

    try:
        dt = datetime.fromisoformat(ts).astimezone(tz)
        return int(dt.hour)
    except (ValueError, TypeError):
        logging.getLogger(__name__).exception("Failed to parse activity timestamp")
        return None
