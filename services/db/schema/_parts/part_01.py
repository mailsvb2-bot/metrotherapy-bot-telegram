from __future__ import annotations

import sqlite3

from services.schema_core import _cols, _add_col


def ensure(c: sqlite3.Connection) -> None:
    """Ensure tables/columns/indexes exist.

    Sections: USERS, SUBSCRIPTIONS, EVENTS (оба формата), JOBS, SELECTED PLAN, GIFT CODES, PROGRESS
    """
    # USERS
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users(
            user_id         INTEGER PRIMARY KEY,
            joined_at       TEXT,
            username        TEXT,
            first_name      TEXT,
            work_time       TEXT,
            home_time       TEXT,
            last_work_date  TEXT,
            last_home_date  TEXT,
            work_index      INTEGER DEFAULT 1,
            home_index      INTEGER DEFAULT 2,
            demo_uses       INTEGER DEFAULT 0
        )
        """
    )
    have = _cols(c, "users")
    need = {
        "joined_at": "joined_at TEXT",
        "username": "username TEXT",
        "first_name": "first_name TEXT",
        "work_time": "work_time TEXT",
        "home_time": "home_time TEXT",
        "last_work_date": "last_work_date TEXT",
        "last_home_date": "last_home_date TEXT",
        "work_index": "work_index INTEGER DEFAULT 1",
        "home_index": "home_index INTEGER DEFAULT 2",
        "demo_uses": "demo_uses INTEGER DEFAULT 0",
    }
    for k, ddl in need.items():
        if k not in have:
            _add_col(c, "users", ddl)

    # SUBSCRIPTIONS
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions(
            user_id        INTEGER PRIMARY KEY,
            plan_type      TEXT,
            total_morning  INTEGER DEFAULT 0,
            total_evening  INTEGER DEFAULT 0,
            used_morning   INTEGER DEFAULT 0,
            used_evening   INTEGER DEFAULT 0,
            status         TEXT DEFAULT 'active',
            started_at     TEXT,
            scope          TEXT,
            expires_at     TEXT,
            created_at     TEXT,
            paid_at        TEXT
        )

        """
    )
    have = _cols(c, "subscriptions")
    for k, ddl in {
        "plan_type": "plan_type TEXT",
        "total_morning": "total_morning INTEGER DEFAULT 0",
        "total_evening": "total_evening INTEGER DEFAULT 0",
        "used_morning": "used_morning INTEGER DEFAULT 0",
        "used_evening": "used_evening INTEGER DEFAULT 0",
        "status": "status TEXT DEFAULT 'active'",
        "started_at": "started_at TEXT",
        "scope": "scope TEXT",
        "expires_at": "expires_at TEXT",
        "created_at": "created_at TEXT",
        "paid_at": "paid_at TEXT",
    }.items():
        if k not in have:
            _add_col(c, "subscriptions", ddl)

    # EVENTS (оба формата)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS events(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            event       TEXT,
            ts          TEXT,
            name        TEXT,
            meta        TEXT,
            created_at  TEXT
        )
        """
    )
    have = _cols(c, "events")
    for k, ddl in {
        "event": "event TEXT",
        "ts": "ts TEXT",
        "name": "name TEXT",
        "meta": "meta TEXT",
        "created_at": "created_at TEXT",
    }.items():
        if k not in have:
            _add_col(c, "events", ddl)
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id)")

    # JOBS
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            job_type    TEXT NOT NULL,
            run_at_utc  TEXT NOT NULL,
            payload     TEXT,
            job_key     TEXT,
            retries     INTEGER DEFAULT 0,
            locked_at   TEXT,
            lock_token  TEXT,
            done_at     TEXT,
            last_error  TEXT
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_run ON jobs(run_at_utc)")

    # v16.4: additive migration for existing DBs.
    have_jobs = _cols(c, "jobs")
    for k, ddl in {
        "job_key": "job_key TEXT",
        "retries": "retries INTEGER DEFAULT 0",
        "locked_at": "locked_at TEXT",
        "lock_token": "lock_token TEXT",
        "done_at": "done_at TEXT",
        "last_error": "last_error TEXT",
    }.items():
        if k not in have_jobs:
            _add_col(c, "jobs", ddl)

    c.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_job_key
        ON jobs(job_key)
        WHERE job_key IS NOT NULL
        """
    )

    # SELECTED PLAN
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS selected_plan(
            user_id     INTEGER PRIMARY KEY,
            scope       TEXT,
            days        INTEGER,
            title       TEXT,
            price       INTEGER,
            plan_code   TEXT,
            chosen_at   TEXT
        )
        """
    )
    # selected_plan: store plan_id as the only source of truth (derived fields may exist for backward compat)
    if "plan_id" not in _cols(c, "selected_plan"):
        _add_col(c, "selected_plan", "plan_id INTEGER")

    # GIFT CODES
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS gift_codes(
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            code         TEXT UNIQUE NOT NULL,
            scope        TEXT NOT NULL,
            days         INTEGER NOT NULL,
            created_by   INTEGER NOT NULL,
            recipient_id INTEGER,
            created_at   TEXT,
            paid         INTEGER DEFAULT 0,
            redeemed_by  INTEGER,
            redeemed_at  TEXT
        )
        """
    )

    # gift_codes: explicit transfer-of-rights state machine
    have_gifts = _cols(c, "gift_codes")
    for k, ddl in {
        "plan_id": "plan_id INTEGER",
        "status": "status TEXT NOT NULL DEFAULT 'created'",
        "claimed_by": "claimed_by INTEGER",
        "claimed_at": "claimed_at TEXT",
        "activated_at": "activated_at TEXT",
        "paid_payment_id": "paid_payment_id TEXT",
        "expires_at": "expires_at TEXT",
    }.items():
        if k not in have_gifts:
            _add_col(c, "gift_codes", ddl)

    have = _cols(c, "gift_codes")
    for k, ddl in {
        "recipient_id": "recipient_id INTEGER",
        "paid": "paid INTEGER DEFAULT 0",
        "redeemed_by": "redeemed_by INTEGER",
        "redeemed_at": "redeemed_at TEXT",
    }.items():
        if k not in have:
            _add_col(c, "gift_codes", ddl)

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals(
            referred_id  INTEGER PRIMARY KEY,
            referrer_id  INTEGER NOT NULL,
            joined_at    TEXT,
            reward_given INTEGER DEFAULT 0,
            reward_days  INTEGER
        )
        """
    )

    # PROGRESS
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS progress(
            user_id     INTEGER NOT NULL,
            scope       TEXT NOT NULL,
            idx         INTEGER DEFAULT 0,
            updated_at  TEXT,
            PRIMARY KEY(user_id, scope)
        )
        """
    )

