from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from services.accounts.identity import ensure_account
from services.db import db, tx
from services.practice_token_lots import create_lot_in_conn
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
    "practice_token_lots",
    "practice_reservation_lots",
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
    """Resolve an already-canonical id without cross-platform guessing.

    Entry points are responsible for resolving (platform, external_user_id) to a
    canonical account.  Looking up a bare number across all messenger platforms
    is unsafe because identical numbers can belong to different people.
    """

    uid = int(user_id)
    with db() as conn:
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
    tokens = int(amount)
    if tokens <= 0:
        raise ValueError("practice_grant_amount_must_be_positive")
    key = idempotency_key or f"grant:{provider}:{provider_payment_id}:{uid}:{package_id}:{tokens}"
    with db() as conn:
        with tx(conn):
            ensure_wallet(conn, uid)
            existing = conn.execute("SELECT id FROM practice_ledger WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                return False, get_wallet_in_conn(conn, uid), int(existing["id"])
            wallet = get_wallet_in_conn(conn, uid)
            balance = int(wallet.available_tokens) + tokens
            conn.execute(
                "UPDATE practice_wallets SET available_tokens=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (balance, uid),
            )
            ledger_id = insert_ledger(
                conn, user_id=uid, event_type="grant", amount=tokens, balance_after=balance,
                reason="payment_succeeded", source=source, package_id=package_id,
                provider=provider, provider_payment_id=provider_payment_id, idempotency_key=key,
            )
            create_lot_in_conn(
                conn, lot_key=key, user_id=uid, provider=provider,
                provider_payment_id=provider_payment_id, package_id=package_id,
                amount=tokens, refundable=bool(provider_payment_id and provider != "manual"),
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
    key = f"payment_grant:{provider}:{provider_payment_id}"

    def _validated_existing(row: Any) -> int | None:
        if (
            int(row["user_id"]) != uid
            or str(row["package_id"]) != package.package_id
            or int(row["tokens_granted"]) != int(package.tokens)
        ):
            raise RuntimeError("payment_token_grant_idempotency_conflict")
        ledger_value = row["ledger_id"]
        if ledger_value is None:
            raise RuntimeError("payment_token_grant_partial_state")
        return int(ledger_value)

    with db() as conn:
        with tx(conn):
            ensure_wallet(conn, uid)
            existing = conn.execute(
                """
                SELECT user_id, package_id, tokens_granted, ledger_id
                FROM payment_token_grants
                WHERE provider=? AND provider_payment_id=?
                LIMIT 1
                """.strip(),
                (provider, provider_payment_id),
            ).fetchone()
            if existing:
                ledger_id = _validated_existing(existing)
                return False, get_wallet_in_conn(conn, uid), ledger_id

            ledger = conn.execute(
                """
                SELECT id, user_id, amount, package_id, provider, provider_payment_id
                FROM practice_ledger
                WHERE idempotency_key=?
                LIMIT 1
                """.strip(),
                (key,),
            ).fetchone()
            lot = conn.execute(
                """
                SELECT id, user_id, package_id, granted_tokens
                FROM practice_token_lots
                WHERE lot_key=?
                LIMIT 1
                """.strip(),
                (key,),
            ).fetchone()

            # A process may have committed the atomic wallet/ledger/lot grant but
            # lost only the denormalized payment marker in a later repair or
            # manual operation. Recreate that marker without granting again.
            if ledger is not None or lot is not None:
                if ledger is None or lot is None:
                    raise RuntimeError("payment_token_grant_partial_state")
                matches = (
                    int(ledger["user_id"]) == uid
                    and int(ledger["amount"]) == int(package.tokens)
                    and str(ledger["package_id"] or "") == package.package_id
                    and str(ledger["provider"] or "") == str(provider)
                    and str(ledger["provider_payment_id"] or "") == str(provider_payment_id)
                    and int(lot["user_id"]) == uid
                    and str(lot["package_id"] or "") == package.package_id
                    and int(lot["granted_tokens"]) == int(package.tokens)
                )
                if not matches:
                    raise RuntimeError("payment_token_grant_idempotency_conflict")
                ledger_id = int(ledger["id"])
                conn.execute(
                    """
                    INSERT INTO payment_token_grants(
                        provider, provider_payment_id, user_id, package_id,
                        tokens_granted, ledger_id
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(provider, provider_payment_id) DO NOTHING
                    """.strip(),
                    (
                        provider,
                        provider_payment_id,
                        uid,
                        package.package_id,
                        package.tokens,
                        ledger_id,
                    ),
                )
                restored = conn.execute(
                    """
                    SELECT user_id, package_id, tokens_granted, ledger_id
                    FROM payment_token_grants
                    WHERE provider=? AND provider_payment_id=?
                    LIMIT 1
                    """.strip(),
                    (provider, provider_payment_id),
                ).fetchone()
                if restored is None:
                    raise RuntimeError("payment_token_grant_marker_restore_failed")
                return False, get_wallet_in_conn(conn, uid), _validated_existing(restored)

            # Claim the provider payment inside the same transaction before any
            # wallet mutation. A concurrent duplicate blocks on the unique key
            # and then observes the completed marker instead of granting twice.
            claimed = conn.execute(
                """
                INSERT INTO payment_token_grants(
                    provider, provider_payment_id, user_id, package_id,
                    tokens_granted, ledger_id
                ) VALUES(?,?,?,?,?,NULL)
                ON CONFLICT(provider, provider_payment_id) DO NOTHING
                """.strip(),
                (provider, provider_payment_id, uid, package.package_id, package.tokens),
            )
            if int(getattr(claimed, "rowcount", 0) or 0) <= 0:
                concurrent = conn.execute(
                    """
                    SELECT user_id, package_id, tokens_granted, ledger_id
                    FROM payment_token_grants
                    WHERE provider=? AND provider_payment_id=?
                    LIMIT 1
                    """.strip(),
                    (provider, provider_payment_id),
                ).fetchone()
                if concurrent is None:
                    raise RuntimeError("payment_token_grant_claim_failed")
                return False, get_wallet_in_conn(conn, uid), _validated_existing(concurrent)

            wallet = get_wallet_in_conn(conn, uid)
            balance = int(wallet.available_tokens) + int(package.tokens)
            conn.execute(
                "UPDATE practice_wallets SET available_tokens=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (balance, uid),
            )
            ledger_id = insert_ledger(
                conn,
                user_id=uid,
                event_type="grant",
                amount=package.tokens,
                balance_after=balance,
                reason="payment_succeeded",
                source=source,
                package_id=package.package_id,
                provider=provider,
                provider_payment_id=provider_payment_id,
                idempotency_key=key,
            )
            create_lot_in_conn(
                conn,
                lot_key=key,
                user_id=uid,
                provider=provider,
                provider_payment_id=provider_payment_id,
                package_id=package.package_id,
                amount=package.tokens,
                refundable=True,
            )
            updated = conn.execute(
                """
                UPDATE payment_token_grants
                SET ledger_id=?
                WHERE provider=? AND provider_payment_id=? AND ledger_id IS NULL
                """.strip(),
                (ledger_id, provider, provider_payment_id),
            )
            if int(getattr(updated, "rowcount", 0) or 0) <= 0:
                raise RuntimeError("payment_token_grant_finalize_failed")
            wallet_after = get_wallet_in_conn(conn, uid)
    return True, wallet_after, ledger_id


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
