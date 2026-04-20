from __future__ import annotations
import logging

import sqlite3

from services.schema_core import _cols, _add_col


def ensure(c: sqlite3.Connection) -> None:
    """Ensure tables/columns/indexes exist.

    Sections: DEMO EVENTS, USER STATE LOG (для диагностики и аналитики), AI DECISIONS (доказуемость: фиксируем решения AI и рекомендации), PLANS (тарифы в БД), _add_col ожидает единый DDL-фрагмент вида: "col_name TYPE [DEFAULT ...]", USER SETTINGS (город/координаты для погоды и UX-настроек)
    """
    # DEMO EVENTS
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS demo_events(
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id            INTEGER NOT NULL,
            kind               TEXT NOT NULL,
            message_id         INTEGER NOT NULL,
            sent_at_utc        TEXT NOT NULL,
            voice_duration_sec INTEGER,
            ack_at_utc         TEXT,
            ack_delay_sec      INTEGER
        )
        """
    )

    # USER STATE LOG (для диагностики и аналитики)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_state_log(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            state       TEXT NOT NULL,
            ts          TEXT NOT NULL,
            meta        TEXT
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_state_user_ts ON user_state_log(user_id, ts)")

    # AI DECISIONS (доказуемость: фиксируем решения AI и рекомендации)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,              -- funnel_profile / price_reco / ...
            value TEXT,
            meta TEXT,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_ai_kind_ts ON ai_decisions(kind, created_at_utc)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ai_user_ts ON ai_decisions(user_id, created_at_utc)")

    # PLANS (тарифы в БД)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS plans(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT UNIQUE NOT NULL,
            scope       TEXT NOT NULL,
            plan_type   TEXT,
            touches     INTEGER,
            days        INTEGER NOT NULL,
            title       TEXT NOT NULL,
            price       INTEGER,
            is_active   INTEGER NOT NULL DEFAULT 1,
            sort        INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_plans_active_sort ON plans(is_active, sort)")

    # Мягкие миграции: добавляем новые колонки, если база создана старой версией
    # _add_col ожидает единый DDL-фрагмент вида: "col_name TYPE [DEFAULT ...]"
    _add_col(c, "plans", "plan_type TEXT")
    _add_col(c, "plans", "touches INTEGER")
    _add_col(c, "plans", "updated_at TEXT")

    # Заполняем значениями по умолчанию, чтобы код работал без "пустых" колонок
    try:
        c.execute(
            "UPDATE plans SET plan_type = COALESCE(NULLIF(plan_type,''), scope) "
            "WHERE plan_type IS NULL OR plan_type = ''"
        )
        c.execute(
            "UPDATE plans SET touches = COALESCE(touches, CASE WHEN days >= 20 THEN 20 ELSE 5 END) "
            "WHERE touches IS NULL OR touches = 0"
        )
        c.execute(
            "UPDATE plans SET updated_at = COALESCE(NULLIF(updated_at,''), created_at) "
            "WHERE updated_at IS NULL OR updated_at = ''"
        )
    except sqlite3.Error:
        logging.getLogger(__name__).exception("Failed to normalize plans defaults (non-fatal)")
        # если таблица ещё пуста/создаётся впервые — ничего страшного
        pass


    # USER SETTINGS (город/координаты для погоды и UX-настроек)
    # Важно: некоторые модули (services/weather.py) ожидают именно user_settings.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings(
            user_id INTEGER PRIMARY KEY,
            city TEXT,
            lat REAL,
            lon REAL,
            updated_at REAL
        )
        """
    )
    have_us = _cols(c, "user_settings")
    for k, ddl in {
        "city": "city TEXT",
        "lat": "lat REAL",
        "lon": "lon REAL",
        "updated_at": "updated_at REAL",
    }.items():
        if k not in have_us:
            _add_col(c, "user_settings", ddl)

