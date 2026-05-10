from __future__ import annotations

import sqlite3

from services.schema_core import _cols, _add_col


def ensure(c: sqlite3.Connection) -> None:
    """Ensure tables/columns/indexes exist.

    Sections: WEATHER PREFS (legacy/compat), PAYMENTS (идемпотентность успешных платежей), FUNNEL EVENTS 2.0 (сценарии, idempotency, аналитика), DAILY AUDIO LOG (защита от повторной отправки утро/вечер в один день), MOOD / SELF-ASSESSMENT (до/после транса), UX: оценка быстрыми кнопками, без ввода текста.
    """
    # WEATHER PREFS (legacy/compat)
    # Оставляем таблицу, если она уже была в базе, но новым кодом не пользуемся.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_prefs(
            user_id INTEGER PRIMARY KEY,
            lat REAL,
            lon REAL,
            city TEXT,
            updated_at TEXT
        )
        """
    )

    # PAYMENTS (идемпотентность успешных платежей + provider reconciliation)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_charge_id TEXT NOT NULL UNIQUE,
            provider_charge_id TEXT,
            payload TEXT,
            amount INTEGER,
            currency TEXT,
            created_at TEXT
        )
        """
    )

    # Payments attribution (Decision Sovereignty / Reward causal link)
    _add_col(c, 'payments', 'decision_id TEXT')
    _add_col(c, 'payments', 'correlation_id TEXT')

    # Provider reconciliation fields. They keep external payment state visible
    # without creating a second payment brain or changing Telegram polling mode.
    _add_col(c, 'payments', 'provider_status TEXT')
    _add_col(c, 'payments', 'provider_event_id TEXT')
    _add_col(c, 'payments', 'provider_raw TEXT')
    _add_col(c, 'payments', 'reconciled_at TEXT')
    _add_col(c, 'payments', 'problem TEXT')
    c.execute("CREATE INDEX IF NOT EXISTS idx_payments_provider_charge_id ON payments(provider_charge_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_payments_provider_status ON payments(provider_status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_payments_problem ON payments(problem)")

    # FUNNEL EVENTS 2.0 (сценарии, idempotency, аналитика)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS funnel_events(
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            scenario_key TEXT NOT NULL,
            sent_at_utc  TEXT NOT NULL,
            meta         TEXT
        )
        """
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_funnel_events_user_scenario ON funnel_events(user_id, scenario_key)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_funnel_events_scenario_sent ON funnel_events(scenario_key, sent_at_utc)"
    )

    # DAILY AUDIO LOG (защита от повторной отправки утро/вечер в один день)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_audio_log(
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            slot TEXT NOT NULL,
            anchor_id INTEGER,
            sent_at_utc TEXT,
            PRIMARY KEY(user_id, day, slot)
        )
        """
    )

    # MOOD / SELF-ASSESSMENT (до/после транса)
    # UX: оценка быстрыми кнопками, без ввода текста.
    # Данные не обнуляются — история накапливается.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS mood_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,               -- work/home
            source TEXT NOT NULL,             -- auto/demo
            day TEXT NOT NULL,                -- local day ISO (YYYY-MM-DD)
            slot TEXT,
            scheduled_at TEXT,
                -- morning/evening/demo
            anchor_id INTEGER,
            pre_score INTEGER,
            post_score INTEGER,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_mood_user_day ON mood_sessions(user_id, day)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mood_user_kind ON mood_sessions(user_id, kind)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mood_user_created ON mood_sessions(user_id, created_at_utc)")

