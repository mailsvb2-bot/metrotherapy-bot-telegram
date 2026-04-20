from __future__ import annotations
import logging

import sqlite3

from services.schema_core import _cols, _add_col


def ensure(c: sqlite3.Connection) -> None:
    """Ensure tables/columns/indexes exist.

    Sections: Telegram file_id cache for audio/voice (ускоряет отправку, особенно в callback-сценариях)., Seed plans, если таблица пустая, (без разрушения данных, только INSERT OR IGNORE)., TEAM ROLES (support/marketing/admin), CUSTOM FUNNEL COPIES (AI-копирайтер/маркетинг), --- Behavioral analytics (no medical labels) ---
    """
    # Telegram file_id cache for audio/voice (ускоряет отправку, особенно в callback-сценариях).
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS audio_cache(
            path TEXT NOT NULL,
            kind TEXT NOT NULL,
            file_id TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY(path, kind)
        )
        """
    )
    have = _cols(c, "users")
    for k, ddl in {
        "trial_used": "trial_used INTEGER DEFAULT 0",
        "trial_scope": "trial_scope TEXT",
        "trial_expires_at": "trial_expires_at TEXT",
    }.items():
        if k not in have:
            _add_col(c, "users", ddl)

    # Seed plans, если таблица пустая
    try:
        cnt = c.execute("SELECT COUNT(1) AS n FROM plans").fetchone()["n"]
    except sqlite3.Error:
        cnt = 0
    if not cnt:
        # Seed plans (цены в рублях; админ может поменять в админке без перезапуска)
        defaults = [
            ("morning_5", "morning", 5, "Утро — 5 дней", 990, 10),
            ("morning_20", "morning", 20, "Утро — 20 дней", 3500, 20),
            ("evening_5", "evening", 5, "Вечер — 5 дней", 990, 30),
            ("evening_20", "evening", 20, "Вечер — 20 дней", 3500, 40),
            ("both_5", "both", 5, "Утро+Вечер — 5 дней", 4900, 55),
            ("both_20", "both", 20, "Утро+Вечер — 20 дней", 7900, 60),
        ]
        for code, scope, days, title, price, sort in defaults:
            c.execute(
                """
                INSERT INTO plans(code, scope, days, title, price, is_active, sort)
                VALUES(?,?,?,?,?,?,?)
                """,
                (code, scope, int(days), title, price, 1, int(sort)),
            )

    # Гарантируем наличие новых тарифов при обновлении на старой базе
    # (без разрушения данных, только INSERT OR IGNORE).

    # Отключаем старые комбинированные тарифы (7/30 дней)
    c.execute("UPDATE plans SET is_active=0 WHERE scope='both' AND days IN (7,30)")

    # Гарантируем актуальный тариф Утро+Вечер — 5 дней (если цена уже задана — не затираем)
    title = "Утро+Вечер — 5 дней"
    default_price = 4900
    c.execute(
        """
        INSERT OR IGNORE INTO plans(code, scope, days, title, price, is_active, sort)
        VALUES(?,?,?,?,?,?,?)
        """,
        ("both_5", "both", 5, title, default_price, 1, 55),
    )
    # Важно: не затираем кастомные названия, которые админ мог изменить.
    # Ранее мы принудительно ставили title каждый старт, и после рестарта
    # ввод админа (по названию) мог перестать матчиться с тем, что он вводил раньше.
    c.execute(
        "UPDATE plans SET scope=?, days=?, title=COALESCE(NULLIF(title,''), ?), price=COALESCE(price, ?), is_active=1, sort=? WHERE code=?",
        ("both", 5, title, default_price, 55, "both_5"),
    )

    # Финальный backfill планов (на случай, если база новая и строки вставились после миграций)
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
        logging.getLogger(__name__).exception("Schema init failed (non-fatal)")


    # TEAM ROLES (support/marketing/admin)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_roles(
            user_id INTEGER NOT NULL,
            role    TEXT NOT NULL,
            PRIMARY KEY(user_id, role)
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role)")

    # CUSTOM FUNNEL COPIES (AI-копирайтер/маркетинг)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS funnel_copies(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT NOT NULL,
            variant     TEXT NOT NULL,
            text        TEXT NOT NULL,
            created_by  INTEGER,
            created_at  TEXT,
            is_active   INTEGER DEFAULT 1
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_funnel_copies_key ON funnel_copies(key)")

    # --- Behavioral analytics (no medical labels) ---
