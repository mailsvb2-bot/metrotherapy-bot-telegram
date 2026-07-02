from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from services.accounts.identity import ensure_account
from services.db import db, tx
from services.practice_tokens import ensure_schema


@dataclass(frozen=True)
class AccountPracticeWalletBackfillPlan:
    target_account_id: int
    source_user_ids: list[int]
    idempotency_key: str
    already_applied: bool
    existing_available_tokens: int
    source_available_tokens: int
    planned_available_tokens: int
    source_reserved_tokens: int
    source_used_tokens: int
    source_refunded_tokens: int
    source_wallets: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_account_id": int(self.target_account_id),
            "source_user_ids": [int(value) for value in self.source_user_ids],
            "idempotency_key": self.idempotency_key,
            "already_applied": bool(self.already_applied),
            "existing_available_tokens": int(self.existing_available_tokens),
            "source_available_tokens": int(self.source_available_tokens),
            "planned_available_tokens": int(self.planned_available_tokens),
            "source_reserved_tokens": int(self.source_reserved_tokens),
            "source_used_tokens": int(self.source_used_tokens),
            "source_refunded_tokens": int(self.source_refunded_tokens),
            "source_wallets": self.source_wallets,
            "warnings": self.warnings,
        }


def _idempotency_key(target_account_id: int, source_user_ids: list[int]) -> str:
    unique_sources = ",".join(str(int(value)) for value in sorted(set(source_user_ids)))
    return f"account_practice_wallet_backfill:{int(target_account_id)}:{unique_sources}"


def _raw_wallet_rows(conn: Any, user_ids: list[int]) -> list[dict[str, Any]]:
    ids = [int(value) for value in user_ids]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT
            user_id,
            available_tokens,
            reserved_tokens,
            used_tokens,
            COALESCE(refunded_tokens, 0) AS refunded_tokens,
            updated_at
        FROM practice_wallets
        WHERE user_id IN ({placeholders})
        ORDER BY user_id
        """.strip(),
        tuple(ids),
    ).fetchall()
    return [dict(row) for row in rows]


def _raw_available_for_user(conn: Any, user_id: int) -> int:
    row = conn.execute(
        "SELECT available_tokens FROM practice_wallets WHERE user_id=?",
        (int(user_id),),
    ).fetchone()
    return int(row["available_tokens"]) if row else 0


def _ledger_exists(conn: Any, idempotency_key: str) -> bool:
    row = conn.execute(
        "SELECT id FROM practice_ledger WHERE idempotency_key=? LIMIT 1",
        (idempotency_key,),
    ).fetchone()
    return row is not None


def build_account_practice_wallet_backfill_plan(
    target_account_id: int,
    source_user_ids: list[int],
) -> AccountPracticeWalletBackfillPlan:
    target = ensure_account(int(target_account_id))
    sources = [int(value) for value in source_user_ids]
    if not sources:
        raise ValueError("source_user_ids must not be empty")

    key = _idempotency_key(target, sources)
    with db() as conn:
        ensure_schema(conn)
        wallets = _raw_wallet_rows(conn, sorted(set([target, *sources])))
        already_applied = _ledger_exists(conn, key)
        existing_available = _raw_available_for_user(conn, target)

    source_wallets = [row for row in wallets if int(row["user_id"]) != target]
    source_available = sum(int(row.get("available_tokens") or 0) for row in source_wallets)
    source_reserved = sum(int(row.get("reserved_tokens") or 0) for row in source_wallets)
    source_used = sum(int(row.get("used_tokens") or 0) for row in source_wallets)
    source_refunded = sum(int(row.get("refunded_tokens") or 0) for row in source_wallets)

    warnings: list[str] = []
    if source_reserved:
        warnings.append("source_reserved_tokens_are_not_transferred")
    if source_used:
        warnings.append("source_used_tokens_are_reported_only")
    if source_refunded:
        warnings.append("source_refunded_tokens_are_reported_only")

    planned_available = existing_available if already_applied else existing_available + source_available

    return AccountPracticeWalletBackfillPlan(
        target_account_id=target,
        source_user_ids=sources,
        idempotency_key=key,
        already_applied=already_applied,
        existing_available_tokens=existing_available,
        source_available_tokens=source_available,
        planned_available_tokens=planned_available,
        source_reserved_tokens=source_reserved,
        source_used_tokens=source_used,
        source_refunded_tokens=source_refunded,
        source_wallets=source_wallets,
        warnings=warnings,
    )


def apply_account_practice_wallet_backfill(
    target_account_id: int,
    source_user_ids: list[int],
) -> AccountPracticeWalletBackfillPlan:
    plan = build_account_practice_wallet_backfill_plan(target_account_id, source_user_ids)
    if plan.already_applied or plan.source_available_tokens <= 0:
        return plan

    target = int(plan.target_account_id)
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens)
                VALUES(?,?,?,?)
                """.strip(),
                (target, 0, 0, 0),
            )
            conn.execute(
                """
                UPDATE practice_wallets
                SET available_tokens=?, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=?
                """.strip(),
                (int(plan.planned_available_tokens), target),
            )
            conn.execute(
                """
                INSERT INTO practice_ledger(
                    user_id, event_type, amount, balance_after, reason, source,
                    package_id, provider, provider_payment_id, idempotency_key
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """.strip(),
                (
                    target,
                    "account_wallet_backfill",
                    int(plan.source_available_tokens),
                    int(plan.planned_available_tokens),
                    "account_merge_practice_wallet_backfill",
                    "account_merge",
                    None,
                    "internal",
                    f"account:{target}",
                    plan.idempotency_key,
                ),
            )

    return build_account_practice_wallet_backfill_plan(target_account_id, source_user_ids)


def plan_to_json_payload(mode: str, plan: AccountPracticeWalletBackfillPlan) -> str:
    return json.dumps({"mode": mode, "plan": plan.to_dict()}, ensure_ascii=False, indent=2, sort_keys=True)
