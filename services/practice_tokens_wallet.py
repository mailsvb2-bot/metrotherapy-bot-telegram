from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from services.accounts.identity import ensure_account
from services.db import db, tx
from services.practice_token_contract import (
    PracticePackage,
    normalize_delivery_mode,
    package_by_id,
    public_practice_packages,
)

_REQUIRED_SCHEMA_TABLES = frozenset({
    "practice_wallets",
    "practice_ledger",
    "payment_token_grants",
    "user_practice_preferences",
    "practice_reservations",
})

EMPTY_BALANCE_MESSAGE = (
    "🔐 На балансе нет доступных практик. "
    "Откройте «💳 Пакеты практик», чтобы продолжить маршрут."
)
RESERVE_FAILED_MESSAGE = "⚠️ Не удалось зарезервировать практику. Попробуйте ещё раз."


@dataclass(frozen=True)
class PracticeWallet:
    user_id: int
    available_tokens: int = 0
    reserved_tokens: int = 0
    used_tokens: int = 0


@dataclass(frozen=True)
class PracticeAccessDecision:
    allowed: bool
    mode: str
    reason: str
    reservation_id: str | None = None
    message: str = ""
    warning: str = ""


def token_economy_enabled() -> bool:
    raw = (os.getenv("TOKEN_ECONOMY_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def enforcement_mode() -> str:
    raw = os.getenv("TOKEN_ENFORCEMENT_MODE")
    if raw is None or not raw.strip():
        app_env = (os.getenv("APP_ENV") or "dev").strip().lower()
        return "soft" if app_env in {"prod", "production"} else "off"
    normalized = raw.strip().lower()
    if normalized in {"hard", "1", "true", "on"}:
        return "hard"
    if normalized in {"soft", "warn", "warning"}:
        return "soft"
    return "off"


def token_access_authoritative() -> bool:
    return token_economy_enabled() and enforcement_mode() != "off"


def ensure_schema(conn: Any) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    existing = {str(row["name"] if hasattr(row, "keys") else row[0]) for row in rows}
    missing = sorted(_REQUIRED_SCHEMA_TABLES - existing)
    if missing:
        raise RuntimeError(f"practice_token_schema_not_migrated:{','.join(missing)}")


def get_active_packages() -> tuple[PracticePackage, ...]:
    return public_practice_packages()


def get_package(package_id: str | None) -> PracticePackage:
    return package_by_id(package_id)


def canonical_practice_user_id(user_id: int) -> int:
    uid = int(user_id)
    external = str(uid)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT account_id
            FROM account_channel_identities
            WHERE external_user_id=?
            ORDER BY account_id
            """.strip(),
            (external,),
        ).fetchall()
        account_ids = [int(row["account_id"]) for row in rows]
        if len(account_ids) == 1:
            return account_ids[0]
        row = conn.execute(
            "SELECT account_id FROM accounts WHERE account_id=? LIMIT 1",
            (uid,),
        ).fetchone()
        if row is not None:
            return int(row["account_id"])
    return ensure_account(uid)


def wallet_from_row(row: Any, user_id: int) -> PracticeWallet:
    if not row:
        return PracticeWallet(int(user_id), 0, 0, 0)
    return PracticeWallet(
        int(row["user_id"]),
        int(row["available_tokens"]),
        int(row["reserved_tokens"]),
        int(row["used_tokens"]),
    )


def ensure_wallet(conn: Any, user_id: int) -> None:
    ensure_schema(conn)
    conn.execute(
        "INSERT OR IGNORE INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
        (int(user_id), 0, 0, 0),
    )


def get_wallet_in_conn(conn: Any, user_id: int) -> PracticeWallet:
    row = conn.execute(
        "SELECT user_id, available_tokens, reserved_tokens, used_tokens FROM practice_wallets WHERE user_id=?",
        (int(user_id),),
    ).fetchone()
    return wallet_from_row(row, int(user_id))


def get_wallet_raw(user_id: int) -> PracticeWallet:
    uid = canonical_practice_user_id(int(user_id))
    with db() as conn:
        ensure_wallet(conn, uid)
        return get_wallet_in_conn(conn, uid)


def insert_ledger(
    conn: Any,
    *,
    user_id: int,
    event_type: str,
    amount: int,
    balance_after: int,
    reason: str,
    source: str = "",
    package_id: str | None = None,
    provider: str | None = None,
    provider_payment_id: str | None = None,
    idempotency_key: str,
) -> int:
    conn.execute(
        """
        INSERT INTO practice_ledger(
            user_id, event_type, amount, balance_after, reason, source,
            package_id, provider, provider_payment_id, idempotency_key
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """.strip(),
        (
            int(user_id), event_type, int(amount), int(balance_after), reason,
            source, package_id, provider, provider_payment_id, idempotency_key,
        ),
    )
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(row["id"] if row else 0)


def grant_tokens(
    user_id: int,
    *,
    package_id: str,
    amount: int,
    provider: str = "manual",
    provider_payment_id: str = "",
    source: str = "",
    idempotency_key: str | None = None,
) -> tuple[bool, PracticeWallet, int | None]:
    uid = canonical_practice_user_id(int(user_id))
    key = idempotency_key or f"grant:{provider}:{provider_payment_id}:{uid}:{package_id}:{amount}"
    with db() as conn:
        with tx(conn):
            ensure_wallet(conn, uid)
            existing = conn.execute(
                "SELECT id FROM practice_ledger WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if existing:
                return False, get_wallet_in_conn(conn, uid), int(existing["id"])
            wallet = get_wallet_in_conn(conn, uid)
            balance = int(wallet.available_tokens) + int(amount)
            conn.execute(
                "UPDATE practice_wallets SET available_tokens=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (balance, uid),
            )
            ledger_id = insert_ledger(
                conn,
                user_id=uid,
                event_type="grant",
                amount=int(amount),
                balance_after=balance,
                reason="payment_succeeded",
                source=source,
                package_id=package_id,
                provider=provider,
                provider_payment_id=provider_payment_id,
                idempotency_key=key,
            )
            wallet_after = get_wallet_in_conn(conn, uid)
    return True, wallet_after, ledger_id


def grant_tokens_for_payment(
    *,
    provider: str,
    provider_payment_id: str,
    user_id: int,
    package_id: str,
    source: str = "webhook",
) -> tuple[bool, PracticeWallet, int | None]:
    uid = canonical_practice_user_id(int(user_id))
    package = get_package(package_id)
    with db() as conn:
        with tx(conn):
            ensure_wallet(conn, uid)
            existing = conn.execute(
                "SELECT ledger_id FROM payment_token_grants WHERE provider=? AND provider_payment_id=?",
                (provider, provider_payment_id),
            ).fetchone()
            if existing:
                return False, get_wallet_in_conn(conn, uid), int(existing["ledger_id"] or 0)

    inserted, wallet, ledger_id = grant_tokens(
        uid,
        package_id=package.package_id,
        amount=package.tokens,
        provider=provider,
        provider_payment_id=provider_payment_id,
        source=source,
        idempotency_key=f"payment_grant:{provider}:{provider_payment_id}",
    )
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO payment_token_grants(
                    provider, provider_payment_id, user_id, package_id, tokens_granted, ledger_id
                ) VALUES(?,?,?,?,?,?)
                """.strip(),
                (provider, provider_payment_id, uid, package.package_id, package.tokens, ledger_id),
            )
    return inserted, wallet, ledger_id


def get_delivery_mode(user_id: int) -> str:
    uid = canonical_practice_user_id(int(user_id))
    with db() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT delivery_mode FROM user_practice_preferences WHERE user_id=?",
            (uid,),
        ).fetchone()
    return str(row["delivery_mode"] if row else "single_daily")


def set_delivery_mode(user_id: int, mode: str) -> str:
    uid = canonical_practice_user_id(int(user_id))
    normalized = normalize_delivery_mode(mode)
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO user_practice_preferences(user_id, delivery_mode, updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    delivery_mode=excluded.delivery_mode,
                    updated_at=CURRENT_TIMESTAMP
                """.strip(),
                (uid, normalized),
            )
    return normalized
