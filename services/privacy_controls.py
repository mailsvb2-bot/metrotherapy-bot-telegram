from __future__ import annotations

"""Schema-driven user data export, anonymization and behavioral erasure."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.db import db, tx
from services.privacy_manifest import (
    MANIFEST_VERSION,
    POLICIES,
    PrivacyPolicy,
    policies_by_disposition,
    table_columns,
    validate_privacy_manifest,
)


@dataclass(frozen=True)
class UserDataEraseResult:
    user_id: int
    anonymized_profile: bool
    deleted_tables: dict[str, int]
    retained_tables: tuple[str, ...]
    manifest_version: str = MANIFEST_VERSION


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return dict(row)


def _table_exists(conn: Any, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


def _ownership_where(
    conn: Any,
    policy: PrivacyPolicy,
    user_id: int,
) -> tuple[str, tuple[int, ...]] | None:
    available = table_columns(conn, policy.table)
    selected = tuple(
        column
        for column in policy.ownership_columns
        if column in available
    )
    if not selected:
        return None
    clause = " OR ".join(f"{column}=?" for column in selected)
    return clause, tuple(int(user_id) for _ in selected)


def _delete_owned_rows(conn: Any, policy: PrivacyPolicy, user_id: int) -> int:
    ownership = _ownership_where(conn, policy, user_id)
    if ownership is None:
        raise RuntimeError(f"privacy_manifest_runtime_column_missing:{policy.table}")
    clause, params = ownership
    cursor = conn.execute(
        f"DELETE FROM {policy.table} WHERE {clause}",  # nosec B608 - validated manifest only
        params,
    )
    return max(0, int(getattr(cursor, "rowcount", 0) or 0))


def _select_owned_rows(
    conn: Any,
    policy: PrivacyPolicy,
    user_id: int,
) -> list[dict[str, Any]]:
    ownership = _ownership_where(conn, policy, user_id)
    if ownership is None:
        raise RuntimeError(f"privacy_manifest_runtime_column_missing:{policy.table}")
    clause, params = ownership
    rows = conn.execute(
        f"SELECT * FROM {policy.table} WHERE {clause}",  # nosec B608 - validated manifest only
        params,
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _anonymize_owned_rows(conn: Any, policy: PrivacyPolicy, user_id: int) -> bool:
    ownership = _ownership_where(conn, policy, user_id)
    if ownership is None:
        raise RuntimeError(f"privacy_manifest_runtime_column_missing:{policy.table}")
    clause, ownership_params = ownership
    available = table_columns(conn, policy.table)
    null_columns = tuple(
        column
        for column in policy.anonymize_columns
        if column in available
    )
    literal_values = tuple(
        (column, value)
        for column, value in policy.anonymize_literals
        if column in available
    )
    if not null_columns and not literal_values:
        return False

    assignments = [f"{column}=NULL" for column in null_columns]
    assignments.extend(f"{column}=?" for column, _value in literal_values)
    assignment_params = tuple(value for _column, value in literal_values)
    if policy.table == "users" and "demo_uses" in available:
        assignments.append("demo_uses=0")
    cursor = conn.execute(
        f"UPDATE {policy.table} SET {', '.join(assignments)} WHERE {clause}",  # nosec B608 - validated manifest only
        (*assignment_params, *ownership_params),
    )
    return max(0, int(getattr(cursor, "rowcount", 0) or 0)) > 0


def _retained_table_names() -> tuple[str, ...]:
    return tuple(
        sorted(
            policy.table
            for policy in POLICIES.values()
            if policy.disposition in {"retain", "anonymize"}
        )
    )


def export_user_data_snapshot(user_id: int) -> dict[str, Any]:
    """Return every manifest-declared user-owned row before erasure."""

    uid = int(user_id)
    output: dict[str, Any] = {
        "user_id": uid,
        "exported_at_utc": _utc_now_iso(),
        "privacy_manifest_version": MANIFEST_VERSION,
        "tables": {},
    }
    with db() as conn:
        report = validate_privacy_manifest(conn, strict=True)
        output["privacy_manifest_tables"] = list(report.discovered_user_tables)
        for table in sorted(POLICIES):
            policy = POLICIES[table]
            if not _table_exists(conn, table):
                continue
            output["tables"][table] = _select_owned_rows(conn, policy, uid)
    return output


def erase_user_behavioral_data(
    user_id: int,
    *,
    reason: str = "user_request",
) -> UserDataEraseResult:
    """Erase behavioral data and anonymize retained routing/accounting shells."""

    uid = int(user_id)
    deleted: dict[str, int] = {}
    anonymized = False
    retained = _retained_table_names()

    with db() as conn:
        validate_privacy_manifest(conn, strict=True)
        with tx(conn):
            for policy in policies_by_disposition("anonymize"):
                if not _table_exists(conn, policy.table):
                    continue
                anonymized = _anonymize_owned_rows(conn, policy, uid) or anonymized

            for policy in policies_by_disposition("erase"):
                if not _table_exists(conn, policy.table):
                    continue
                deleted[policy.table] = _delete_owned_rows(conn, policy, uid)

            conn.execute(
                """
                INSERT INTO privacy_erasure_log(
                    user_id, erased_at_utc, reason, retained_tables
                ) VALUES(?,?,?,?)
                """.strip(),
                (
                    uid,
                    _utc_now_iso(),
                    str(reason or "user_request"),
                    json.dumps(
                        {
                            "manifest_version": MANIFEST_VERSION,
                            "tables": retained,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )

    return UserDataEraseResult(
        user_id=uid,
        anonymized_profile=anonymized,
        deleted_tables=deleted,
        retained_tables=retained,
    )
