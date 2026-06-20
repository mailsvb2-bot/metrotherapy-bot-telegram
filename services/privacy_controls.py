from __future__ import annotations

"""User data privacy controls.

The project stores behavioral self-reports, events, delivery state and payment
facts. This module gives one canonical place for export/anonymize/erase actions
so admin surfaces and future API endpoints do not invent their own partial data
deletion logic.

Financial/provider facts are intentionally retained by default for accounting
and dispute handling; behavioral/psychological state can be erased separately.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.db import db, tx


_BEHAVIORAL_USER_TABLES: tuple[str, ...] = (
    "events",
    "jobs",
    "idempotency",
    "progress",
    "demo_events",
    "mood_sessions",
    "user_daily_state",
    "user_dynamic_profile",
    "system_reactions_log",
    "sla_metrics",
    "selected_plan",
    "user_audio_timeline",
    "user_audio_access_tokens",
    "practice_token_audit",
    "trial_analytics",
    "audio_progress",
    "messenger_audio_progress",
    "user_channel_links",
    "user_delivery_preferences",
)

_FINANCIAL_RETAINED_TABLES: tuple[str, ...] = (
    "payments",
    "payment_events",
    "subscriptions",
    "gift_codes",
    "practice_token_wallets",
    "practice_token_ledger",
    "premium_entitlements",
    "premium_delivery_outbox",
    "consultation_requests",
)

_USER_PROFILE_COLUMNS: tuple[str, ...] = (
    "username",
    "first_name",
    "work_time",
    "home_time",
    "last_work_date",
    "last_home_date",
)


@dataclass(frozen=True)
class UserDataEraseResult:
    user_id: int
    anonymized_profile: bool
    deleted_tables: dict[str, int]
    retained_tables: tuple[str, ...]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {str(k): row[k] for k in row.keys()}
    return dict(row)


def _table_exists(conn: Any, table: str) -> bool:
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


def _delete_user_rows(conn: Any, table: str, user_id: int) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        # table is selected from the private allow-list above; user_id is parameterized.
        cur = conn.execute(f"DELETE FROM {table} WHERE user_id=?", (int(user_id),))  # nosec B608
    except sqlite3.OperationalError:
        return 0
    try:
        return max(0, int(getattr(cur, "rowcount", 0) or 0))
    except (TypeError, ValueError):
        return 0


def export_user_data_snapshot(user_id: int) -> dict[str, Any]:
    """Return a best-effort data snapshot for a user.

    This is intentionally read-only and includes retained financial tables so an
    operator can explain what will remain after behavioral erasure.
    """
    uid = int(user_id)
    out: dict[str, Any] = {
        "user_id": uid,
        "exported_at_utc": _utc_now_iso(),
        "tables": {},
    }
    with db() as conn:
        for table in ("users", *_BEHAVIORAL_USER_TABLES, *_FINANCIAL_RETAINED_TABLES):
            if not _table_exists(conn, table):
                continue
            try:
                # table is selected from private allow-lists above; user_id is parameterized.
                rows = conn.execute(f"SELECT * FROM {table} WHERE user_id=?", (uid,)).fetchall()  # nosec B608
            except sqlite3.OperationalError:
                continue
            out["tables"][table] = [_row_to_dict(row) for row in rows]
    return out


def erase_user_behavioral_data(user_id: int, *, reason: str = "user_request") -> UserDataEraseResult:
    """Erase behavioral/psychological data while retaining financial facts.

    The profile row is anonymized rather than deleted so foreign-key-free legacy
    tables and admin accounting surfaces keep a stable user_id reference.
    """
    uid = int(user_id)
    deleted: dict[str, int] = {}
    anonymized = False

    with db() as conn:
        with tx(conn):
            if _table_exists(conn, "users"):
                assignments = ", ".join(f"{column}=NULL" for column in _USER_PROFILE_COLUMNS)
                # assignments is built only from the private allow-list above.
                conn.execute(  # nosec B608
                    f"UPDATE users SET {assignments}, demo_uses=0 WHERE user_id=?",
                    (uid,),
                )
                anonymized = True

            for table in _BEHAVIORAL_USER_TABLES:
                deleted[table] = _delete_user_rows(conn, table, uid)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS privacy_erasure_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    erased_at_utc TEXT NOT NULL,
                    reason TEXT,
                    retained_tables TEXT
                )
                """.strip()
            )
            conn.execute(
                "INSERT INTO privacy_erasure_log(user_id, erased_at_utc, reason, retained_tables) VALUES(?,?,?,?)",
                (uid, _utc_now_iso(), str(reason or "user_request"), json.dumps(_FINANCIAL_RETAINED_TABLES)),
            )

    return UserDataEraseResult(
        user_id=uid,
        anonymized_profile=anonymized,
        deleted_tables=deleted,
        retained_tables=_FINANCIAL_RETAINED_TABLES,
    )
