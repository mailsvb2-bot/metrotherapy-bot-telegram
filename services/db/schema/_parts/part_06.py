from __future__ import annotations
import logging

import sqlite3

from services.schema_core import _cols, _add_col


def ensure(c: sqlite3.Connection) -> None:
    """Ensure tables/columns/indexes exist.

    Sections: Raw interaction log (clicks/messages) with delta between actions., Per-user behavioral features (updated incrementally), Funnel / personalization state (no medical labels), Brick history (which micro-blocks were shown), Micro-tests (generic questions) and answers, Seed micro-questions (safe, non-clinical). INSERT OR IGNORE keeps old DB intact.
    """
    # Raw interaction log (clicks/messages) with delta between actions.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS interaction_log(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            kind        TEXT NOT NULL,              -- callback/message/command
            key         TEXT,                       -- cb data prefix / command
            delta_ms    INTEGER,                    -- time since previous action
            ts          TEXT NOT NULL               -- ISO UTC
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_interaction_user_ts ON interaction_log(user_id, ts)")

    # Per-user behavioral features (updated incrementally)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_behavior(
            user_id         INTEGER PRIMARY KEY,
            last_ts         TEXT,
            ema_delta_ms    REAL,       -- exponential moving average
            ema_absdev_ms   REAL,       -- ema of absolute deviation
            profile         TEXT,       -- compressed / sparse / stable
            updated_at      TEXT
        )
        """
    )

    # Funnel / personalization state (no medical labels)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_funnel(
            user_id     INTEGER PRIMARY KEY,
            stage       TEXT,       -- d0..d7, active, upsell, etc.
            variant     TEXT,       -- A/B/C (by rhythm)
            updated_at  TEXT
        )
        """
    )

    # Brick history (which micro-blocks were shown)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_bricks(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            brick_key   TEXT NOT NULL,
            context     TEXT,
            ts          TEXT NOT NULL
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_bricks_user_ts ON user_bricks(user_id, ts)")

    # Micro-tests (generic questions) and answers
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS micro_questions(
            key         TEXT PRIMARY KEY,
            question    TEXT NOT NULL,
            options     TEXT NOT NULL,  -- JSON list
            is_active   INTEGER DEFAULT 1
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS micro_answers(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            q_key       TEXT NOT NULL,
            answer      TEXT NOT NULL,
            ts          TEXT NOT NULL
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_micro_answers_user ON micro_answers(user_id, q_key)")

    # Seed micro-questions (safe, non-clinical). INSERT OR IGNORE keeps old DB intact.
    try:
        import json

        seed = [
            (
                "q_rhythm",
                "Сейчас Вам ближе какой темп?",
                ["Спокойный и ровный", "Чуть быстрее, собраннее", "Медленнее и мягче"],
            ),
            (
                "q_focus",
                "В дороге Вам сейчас важнее…",
                ["Собраться и настроиться", "Разгрузиться и отпустить", "Просто тишина"],
            ),
            (
                "q_body",
                "Где в теле Вы чаще замечаете напряжение?",
                ["Шея/плечи", "Грудь/дыхание", "Живот", "Почти не замечаю"],
            ),
            (
                "body_01",
                "Где прямо сейчас больше всего чувствуется напряжение?",
                ["Шея", "Плечи", "Челюсть", "Поясница"],
            ),
            (
                "body_02",
                "В каком месте тела напряжение заметнее именно сегодня?",
                ["Грудь", "Живот", "Голова", "Плечи"],
            ),
            (
                "body_03",
                "Если выбрать одно место — где оно?",
                ["Шея", "Поясница", "Живот", "Грудь"],
            ),
            (
                "body_04",
                "Где сейчас хочется чуть больше лёгкости?",
                ["Челюсть", "Шея", "Грудь", "Живот"],
            ),
            (
                "body_05",
                "Где напряжение больше мешает в дороге?",
                ["Плечи", "Шея", "Поясница", "Голова"],
            ),
        ]
        for key, q, opts in seed:
            c.execute(
                "INSERT OR IGNORE INTO micro_questions(key, question, options, is_active) VALUES(?,?,?,1)",
                (key, q, json.dumps(opts, ensure_ascii=False)),
            )
    except sqlite3.Error:
        logging.getLogger(__name__).exception("Schema init failed (non-fatal)")

