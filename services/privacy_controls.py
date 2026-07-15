from __future__ import annotations

"""User data privacy controls.

The project stores behavioral self-reports, events, delivery state and payment
facts. This module gives one canonical place for export/anonymize/erase actions
so admin surfaces and future API endpoints do not invent their own partial data
deletion logic.

Financial/provider facts and the minimum routing identity required to honour an
existing purchase are intentionally retained by behavioral erasure. Human-readable
profile fields and behavioral/psychological state are removed.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.db import db, tx


# Tables whose rows are behavioral state and must be removed. Each table lists
# every supported ownership column because the account migration introduced
# account_id while legacy tables still use user_id.
_BEHAVIORAL_TABLE_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("events", ("user_id",)),
    ("jobs", ("user_id",)),
    ("idempotency", ("user_id",)),
    ("progress", ("user_id",)),
    ("demo_events", ("user_id",)),
    ("mood_sessions", ("user_id",)),
    ("user_daily_state", ("user_id",)),
    ("user_dynamic_profile", ("user_id",)),
    ("system_reactions_log", ("user_id",)),
    ("sla_metrics", ("user_id",)),
    ("selected_plan", ("user_id",)),
    ("user_audio_timeline", ("user_id",)),
    ("user_audio_access_tokens", ("user_id",)),
    ("practice_token_audit", ("user_id",)),
    ("trial_analytics", ("user_id",)),
    ("audio_progress", ("user_id",)),
    ("messenger_audio_progress", ("user_id",)),
    ("user_audio_progress", ("user_id",)),
    ("user_channel_links", ("user_id",)),
    ("user_delivery_preferences", ("user_id",)),
    ("user_channel_bridge_tokens", ("user_id", "account_id", "consumed_account_id")),
    ("account_audio_progress", ("account_id",)),
    ("account_audio_deliveries", ("account_id",)),
    ("account_audio_completions", ("account_id",)),
)

# Financial/provider state remains available for accounting, refunds, disputes
# and fulfilment. The keys cover buyer/beneficiary variants used by payment flows.
_RETAINED_TABLE_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("payments", ("user_id",)),
    ("payment_events", ("user_id",)),
    ("subscriptions", ("user_id",)),
    ("gift_codes", ("user_id", "buyer_user_id", "recipient_user_id")),
    ("gift_claims", ("buyer_user_id", "recipient_user_id")),
    ("practice_wallets", ("user_id",)),
    ("practice_ledger", ("user_id",)),
    ("payment_token_grants", ("user_id",)),
    ("practice_reservations", ("user_id",)),
    ("user_practice_preferences", ("user_id",)),
    ("practice_token_lots", ("user_id",)),
    ("premium_entitlements", ("user_id",)),
    ("premium_delivery_outbox", ("user_id",)),
    ("consultation_requests", ("user_id",)),
    ("telegram_stars_refunds", ("payment_user_id", "beneficiary_user_id", "requested_by")),
    # Canonical account routing is retained, but display/profile fields are
    # anonymized below so the paid account can still receive purchased access.
    ("accounts", ("account_id", "primary_user_id")),
    ("account_channel_identities", ("account_id",)),
)

_FINANCIAL_RETAINED_TABLES: tuple[str, ...] = tuple(table for table, _ in _RETAINED_TABLE_KEYS)

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


def _table_columns(conn: Any, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608 - private allow-list only
    except sqlite3.Error:
        return set()
    columns: set[str] = set()
    for row in rows:
        if hasattr(row, "keys"):
            columns.add(str(row["name"]))
        elif len(row) > 1:
            columns.add(str(row[1]))
    return columns


def _ownership_where(conn: Any, table: str, candidate_columns: tuple[str, ...], user_id: int) -> tuple[str, tuple[int, ...]] | None:
    available = _table_columns(conn, table)
    selected = tuple(column for column in candidate_columns if column in available)
    if not selected:
        return None
    clause = " OR ".join(f"{column}=?" for column in selected)
    return clause, tuple(int(user_id) for _ in selected)


def _delete_owned_rows(conn: Any, table: str, candidate_columns: tuple[str, ...], user_id: int) -> int:
    ownership = _ownership_where(conn, table, candidate_columns, user_id)
    if ownership is None:
        return 0
    clause, params = ownership
    try:
        cur = conn.execute(f"DELETE FROM {table} WHERE {clause}", params)  # nosec B608 - private allow-list only
    except sqlite3.OperationalError:
        return 0
    try:
        return max(0, int(getattr(cur, "rowcount", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _select_owned_rows(conn: Any, table: str, candidate_columns: tuple[str, ...], user_id: int) -> list[dict[str, Any]]:
    ownership = _ownership_where(conn, table, candidate_columns, user_id)
    if ownership is None:
        return []
    clause, params = ownership
    try:
        rows = conn.execute(f"SELECT * FROM {table} WHERE {clause}", params).fetchall()  # nosec B608
    except sqlite3.OperationalError:
        return []
    return [_row_to_dict(row) for row in rows]


def export_user_data_snapshot(user_id: int) -> dict[str, Any]:
    """Return a best-effort data snapshot for a user.

    The snapshot includes retained financial/routing rows so an operator can
    explain exactly what will remain after behavioral erasure.
    """
    uid = int(user_id)
    out: dict[str, Any] = {
        "user_id": uid,
        "exported_at_utc": _utc_now_iso(),
        "tables": {},
    }
    with db() as conn:
        if _table_exists(conn, "users"):
            rows = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchall()
            out["tables"]["users"] = [_row_to_dict(row) for row in rows]
        for table, columns in (*_BEHAVIORAL_TABLE_KEYS, *_RETAINED_TABLE_KEYS):
            if table in out["tables"]:
                continue
            if not _table_exists(conn, table):
                continue
            out["tables"][table] = _select_owned_rows(conn, table, columns, uid)
    return out


def erase_user_behavioral_data(user_id: int, *, reason: str = "user_request") -> UserDataEraseResult:
    """Erase behavioral/psychological data while retaining financial facts.

    The legacy users profile and canonical identity display fields are anonymized.
    External platform identifiers remain only as the minimum routing identity
    required to honour already-purchased access and payment support obligations.
    """
    uid = int(user_id)
    deleted: dict[str, int] = {}
    anonymized = False

    with db() as conn:
        with tx(conn):
            user_columns = _table_columns(conn, "users")
            profile_columns = tuple(column for column in _USER_PROFILE_COLUMNS if column in user_columns)
            if profile_columns and "user_id" in user_columns:
                assignments = ", ".join(f"{column}=NULL" for column in profile_columns)
                if "demo_uses" in user_columns:
                    assignments += ", demo_uses=0"
                conn.execute(f"UPDATE users SET {assignments} WHERE user_id=?", (uid,))  # nosec B608
                anonymized = True

            identity_columns = _table_columns(conn, "account_channel_identities")
            if "account_id" in identity_columns:
                assignments = [f"{column}=NULL" for column in ("username", "display_name") if column in identity_columns]
                if assignments:
                    conn.execute(
                        f"UPDATE account_channel_identities SET {', '.join(assignments)} WHERE account_id=?",  # nosec B608
                        (uid,),
                    )
                    anonymized = True

            for table, columns in _BEHAVIORAL_TABLE_KEYS:
                deleted[table] = _delete_owned_rows(conn, table, columns, uid)

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
