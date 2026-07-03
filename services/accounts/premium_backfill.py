from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from services.db import db, tx
from services.premium_entitlements import ensure_schema as ensure_premium_schema

_PREMIUM_TABLES = (
    "premium_entitlements",
    "premium_delivery_outbox",
    "consultation_requests",
)


@dataclass(frozen=True)
class AccountPremiumBackfillPlan:
    target_account_id: int
    source_user_ids: list[int]
    rows_by_table: dict[str, list[dict[str, Any]]]
    total_rows: int
    already_applied: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_account_id": int(self.target_account_id),
            "source_user_ids": [int(value) for value in self.source_user_ids],
            "rows_by_table": self.rows_by_table,
            "counts_by_table": {table: len(rows) for table, rows in self.rows_by_table.items()},
            "total_rows": int(self.total_rows),
            "already_applied": bool(self.already_applied),
        }


def _linked_numeric_user_ids(conn: Any, target_account_id: int) -> list[int]:
    target = int(target_account_id)
    rows = conn.execute(
        """
        SELECT external_user_id
        FROM account_channel_identities
        WHERE account_id=?
        ORDER BY platform
        """.strip(),
        (target,),
    ).fetchall()
    values = {target}
    for row in rows:
        raw = str(row["external_user_id"] or "").strip()
        if raw.isdigit():
            values.add(int(raw))
    return sorted(values)


def _table_rows(conn: Any, table: str, *, target: int, source_ids: list[int]) -> list[dict[str, Any]]:
    if not source_ids:
        return []
    placeholders = ",".join("?" for _ in source_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM {table}
        WHERE user_id IN ({placeholders})
          AND user_id<>?
        ORDER BY user_id, id
        """.strip(),
        tuple(source_ids + [int(target)]),
    ).fetchall()
    return [dict(row) for row in rows]


def build_account_premium_backfill_plan(
    target_account_id: int,
    source_user_ids: list[int] | None = None,
) -> AccountPremiumBackfillPlan:
    target = int(target_account_id)
    with db() as conn:
        ensure_premium_schema(conn)
        sources = sorted(set(int(value) for value in (source_user_ids or _linked_numeric_user_ids(conn, target))))
        if target not in sources:
            sources.append(target)
            sources = sorted(set(sources))
        rows_by_table = {
            table: _table_rows(conn, table, target=target, source_ids=sources)
            for table in _PREMIUM_TABLES
        }
    total = sum(len(rows) for rows in rows_by_table.values())
    return AccountPremiumBackfillPlan(
        target_account_id=target,
        source_user_ids=sources,
        rows_by_table=rows_by_table,
        total_rows=total,
        already_applied=total == 0,
    )


def apply_account_premium_backfill(
    target_account_id: int,
    source_user_ids: list[int] | None = None,
) -> AccountPremiumBackfillPlan:
    plan = build_account_premium_backfill_plan(target_account_id, source_user_ids)
    if plan.already_applied:
        return plan

    target = int(plan.target_account_id)
    with db() as conn:
        with tx(conn):
            ensure_premium_schema(conn)
            for table, rows in plan.rows_by_table.items():
                for row in rows:
                    conn.execute(
                        f"UPDATE {table} SET user_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
                        (target, int(row["id"]), int(row["user_id"])),
                    )

    return build_account_premium_backfill_plan(target_account_id, source_user_ids)


def plan_to_json_payload(mode: str, plan: AccountPremiumBackfillPlan) -> str:
    return json.dumps({"mode": mode, "plan": plan.to_dict()}, ensure_ascii=False, indent=2, sort_keys=True)
