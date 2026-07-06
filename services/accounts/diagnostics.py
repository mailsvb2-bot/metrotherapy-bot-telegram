from __future__ import annotations

import json
from typing import Any

from services.accounts.identity import get_account_snapshot
from services.db import db


def _rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql.strip(), params).fetchall()]


def _one(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql.strip(), params).fetchone()
    return dict(row) if row else None


def _linked_external_ids(snapshot: dict[str, Any]) -> list[int]:
    values: list[int] = []
    for identity in snapshot.get("identities", []):
        raw = str(identity.get("external_user_id") or "").strip()
        if raw.isdigit():
            values.append(int(raw))
    account_id = int(snapshot["account_id"])
    if account_id not in values:
        values.append(account_id)
    return sorted(set(values))


def _canonical_account_id_readonly(conn: Any, user_id: int) -> int:
    external = str(int(user_id))
    rows = conn.execute(
        """
        SELECT DISTINCT account_id
        FROM account_channel_identities
        WHERE external_user_id=?
        ORDER BY account_id
        """,
        (external,),
    ).fetchall()
    account_ids = [int(row["account_id"]) for row in rows]
    if len(account_ids) == 1:
        return account_ids[0]

    row = conn.execute(
        "SELECT account_id FROM accounts WHERE account_id=? LIMIT 1",
        (int(user_id),),
    ).fetchone()
    if row:
        return int(row["account_id"])

    return int(user_id)


def build_account_diagnostics(account_id: int) -> dict[str, Any]:
    target = int(account_id)
    snapshot = get_account_snapshot(target)
    linked_ids = _linked_external_ids(snapshot)
    with db() as conn:
        account_audio_progress = _rows(
            conn,
            """
            SELECT *
            FROM account_audio_progress
            WHERE account_id=?
            ORDER BY product_id, program_id
            """,
            (target,),
        )
        account_audio_deliveries = _rows(
            conn,
            """
            SELECT *
            FROM account_audio_deliveries
            WHERE account_id=?
            ORDER BY id DESC
            LIMIT 20
            """,
            (target,),
        )
        account_audio_completions = _rows(
            conn,
            """
            SELECT *
            FROM account_audio_completions
            WHERE account_id=?
            ORDER BY completed_at DESC, audio_no DESC
            LIMIT 20
            """,
            (target,),
        )

        raw_wallets: list[dict[str, Any]] = []
        raw_preferences: list[dict[str, Any]] = []
        legacy_identities: list[dict[str, Any]] = []
        premium_entitlements: list[dict[str, Any]] = []
        premium_delivery_outbox: list[dict[str, Any]] = []
        consultation_requests: list[dict[str, Any]] = []

        for linked_id in linked_ids:
            params = (int(linked_id),)
            raw_wallets.extend(_rows(
                conn,
                """
                SELECT *
                FROM practice_wallets
                WHERE user_id=?
                ORDER BY user_id
                """,
                params,
            ))
            raw_preferences.extend(_rows(
                conn,
                """
                SELECT *
                FROM user_practice_preferences
                WHERE user_id=?
                ORDER BY user_id
                """,
                params,
            ))
            legacy_identities.extend(_rows(
                conn,
                """
                SELECT *
                FROM user_channel_identities
                WHERE user_id=?
                ORDER BY user_id, platform
                """,
                params,
            ))
            premium_entitlements.extend(_rows(
                conn,
                """
                SELECT *
                FROM premium_entitlements
                WHERE user_id=?
                ORDER BY user_id, id DESC
                """,
                params,
            ))
            premium_delivery_outbox.extend(_rows(
                conn,
                """
                SELECT *
                FROM premium_delivery_outbox
                WHERE user_id=?
                ORDER BY user_id, id DESC
                """,
                params,
            ))
            consultation_requests.extend(_rows(
                conn,
                """
                SELECT *
                FROM consultation_requests
                WHERE user_id=?
                ORDER BY user_id, id DESC
                """,
                params,
            ))

        premium_delivery_outbox.sort(key=lambda row: (int(row.get("user_id") or 0), -int(row.get("id") or 0)))
        premium_delivery_outbox = premium_delivery_outbox[:50]
        consultation_requests.sort(key=lambda row: (int(row.get("user_id") or 0), -int(row.get("id") or 0)))
        consultation_requests = consultation_requests[:50]

        target_wallet = _one(
            conn,
            """
            SELECT *
            FROM practice_wallets
            WHERE user_id=?
            """,
            (target,),
        )

        backfill_ledger = _rows(
            conn,
            """
            SELECT id, user_id, event_type, amount, balance_after, reason, source, provider, provider_payment_id, idempotency_key, created_at
            FROM practice_ledger
            WHERE user_id=?
              AND event_type='account_wallet_backfill'
            ORDER BY id DESC
            LIMIT 10
            """,
            (target,),
        )

        canonical_views = []
        for value in linked_ids:
            canonical = _canonical_account_id_readonly(conn, value)
            wallet = _one(
                conn,
                """
                SELECT *
                FROM practice_wallets
                WHERE user_id=?
                """,
                (canonical,),
            )
            preference = _one(
                conn,
                """
                SELECT *
                FROM user_practice_preferences
                WHERE user_id=?
                """,
                (canonical,),
            )
            canonical_views.append({
                "input_user_id": value,
                "canonical_account_id": canonical,
                "wallet": wallet,
                "delivery_mode": (preference or {}).get("delivery_mode", "single_daily"),
            })

    warnings: list[str] = []
    for wallet in raw_wallets:
        raw_user_id = int(wallet["user_id"])
        if raw_user_id != target and int(wallet.get("available_tokens") or 0) > 0:
            warnings.append(
                f"legacy_source_wallet_has_available_tokens:user_id={raw_user_id}:available={int(wallet.get('available_tokens') or 0)}"
            )

    if not target_wallet:
        warnings.append("target_wallet_missing")
    elif int(target_wallet.get("available_tokens") or 0) <= 0:
        warnings.append("target_wallet_empty")

    if not account_audio_progress:
        warnings.append("account_audio_progress_missing")

    platforms = sorted({str(item.get("platform")) for item in snapshot.get("identities", [])})
    if platforms != ["max", "telegram", "vk"]:
        warnings.append(f"unexpected_identity_platforms:{platforms}")

    return {
        "account_id": target,
        "account": snapshot,
        "linked_user_ids": linked_ids,
        "platforms": platforms,
        "canonical_views": canonical_views,
        "account_audio_progress": account_audio_progress,
        "account_audio_deliveries_tail": account_audio_deliveries,
        "account_audio_completions_tail": account_audio_completions,
        "raw_practice_wallets": raw_wallets,
        "raw_practice_preferences": raw_preferences,
        "legacy_channel_identities": legacy_identities,
        "premium_entitlements": premium_entitlements,
        "premium_delivery_outbox_tail": premium_delivery_outbox,
        "consultation_requests": consultation_requests,
        "practice_wallet_backfill_ledger_tail": backfill_ledger,
        "warnings": warnings,
        "ok": not warnings,
    }


def diagnostics_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
