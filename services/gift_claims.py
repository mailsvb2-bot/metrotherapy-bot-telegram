from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from services.db import db, tx
from services.practice_token_contract import package_by_id
from services.practice_tokens import grant_tokens_for_payment
from services.premium_entitlements import grant_premium_entitlements_for_payment

_GIFT_TOKEN_RE = re.compile(r"^gift_[a-f0-9]{32}$")
_PROTECTED_CLAIM_STATUSES = {"claiming", "claimed"}


@dataclass(frozen=True)
class GiftClaimResult:
    ok: bool
    status: str
    message: str
    package_id: str = ""


def new_gift_token() -> str:
    """Create an opaque universal gift token suitable for /start payloads and public links."""
    return f"gift_{uuid.uuid4().hex}"


def create_gift_checkout_token(
    *,
    buyer_user_id: int,
    package_id: str,
    source_platform: str = "telegram",
    recipient_hint: str = "",
) -> str:
    """Reserve a paid-gift claim row before the buyer leaves Telegram for checkout.

    YooKassa returns only metadata from the payment object. Therefore the public
    checkout URL must already carry a real gift_token and a real buyer id; using
    user_id=0 or creating the token only after payment makes the gift impossible
    to claim safely.
    """
    buyer_id = int(buyer_user_id or 0)
    if buyer_id <= 0:
        raise ValueError("buyer_user_id is required for gift checkout")

    package = package_by_id(package_id)
    platform = (source_platform or "telegram").strip()[:32] or "telegram"
    recipient = str(recipient_hint or "").strip()[:300]
    last_exc: sqlite3.IntegrityError | None = None

    for _ in range(5):
        token = new_gift_token()
        try:
            with db() as conn:
                with tx(conn):
                    conn.execute(
                        """
                        INSERT INTO gift_claims(
                            gift_token, buyer_user_id, package_id, source_platform, recipient_hint, status
                        ) VALUES(?,?,?,?,?, 'created')
                        """.strip(),
                        (token, buyer_id, package.package_id, platform, recipient),
                    )
            return token
        except sqlite3.IntegrityError as exc:
            # uuid4 collisions are effectively impossible, but the loop keeps the
            # DB UNIQUE constraint as the source of truth instead of assuming.
            last_exc = exc
            continue

    raise RuntimeError("failed_to_create_unique_gift_token") from last_exc


def normalize_gift_token(raw: str | None) -> str:
    token = str(raw or "").strip()
    if token.startswith("/start "):
        token = token.split(maxsplit=1)[1].strip()
    if token.startswith("claim "):
        token = token.split(maxsplit=1)[1].strip()
    return token


def is_gift_token(raw: str | None) -> bool:
    return bool(_GIFT_TOKEN_RE.match(normalize_gift_token(raw)))


def mark_gift_paid(
    *,
    gift_token: str,
    buyer_user_id: int,
    package_id: str,
    provider: str,
    provider_payment_id: str,
    source_platform: str,
) -> None:
    token = normalize_gift_token(gift_token)
    if not is_gift_token(token):
        raise ValueError("Invalid gift token")
    package_by_id(package_id)
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                INSERT INTO gift_claims(
                    gift_token, buyer_user_id, package_id, provider,
                    provider_payment_id, source_platform, status, paid_at
                ) VALUES(?,?,?,?,?,?, 'paid', CURRENT_TIMESTAMP)
                ON CONFLICT(gift_token) DO UPDATE SET
                    buyer_user_id=CASE
                        WHEN gift_claims.status IN ('claiming','claimed') THEN gift_claims.buyer_user_id
                        ELSE excluded.buyer_user_id
                    END,
                    package_id=CASE
                        WHEN gift_claims.status IN ('claiming','claimed') THEN gift_claims.package_id
                        ELSE excluded.package_id
                    END,
                    provider=CASE
                        WHEN gift_claims.status IN ('claiming','claimed') THEN gift_claims.provider
                        ELSE excluded.provider
                    END,
                    provider_payment_id=CASE
                        WHEN gift_claims.status IN ('claiming','claimed') THEN gift_claims.provider_payment_id
                        ELSE excluded.provider_payment_id
                    END,
                    source_platform=CASE
                        WHEN gift_claims.status IN ('claiming','claimed') THEN gift_claims.source_platform
                        ELSE excluded.source_platform
                    END,
                    status=CASE
                        WHEN gift_claims.status IN ('claiming','claimed') THEN gift_claims.status
                        ELSE 'paid'
                    END,
                    paid_at=COALESCE(gift_claims.paid_at, CURRENT_TIMESTAMP)
                """.strip(),
                (token, int(buyer_user_id or 0), package_id, provider, provider_payment_id, source_platform),
            )


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    keys = ["gift_token", "buyer_user_id", "recipient_user_id", "package_id", "provider", "provider_payment_id", "source_platform", "status"]
    return {key: row[idx] for idx, key in enumerate(keys)}


def _gift_row_in_conn(conn: Any, token: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT gift_token, buyer_user_id, recipient_user_id, package_id, provider,
               provider_payment_id, source_platform, status
        FROM gift_claims
        WHERE gift_token=?
        """.strip(),
        (token,),
    ).fetchone()
    return _row_to_dict(row)


def _acquire_gift_claim(*, token: str, recipient_user_id: int) -> GiftClaimResult | dict[str, Any]:
    recipient_id = int(recipient_user_id)
    with db() as conn:
        with tx(conn):
            data = _gift_row_in_conn(conn, token)
            if not data:
                return GiftClaimResult(False, "not_paid", "Подарок пока не найден как оплаченный. Проверьте ссылку после успешной оплаты.")

            status = str(data.get("status") or "").strip()
            package_id = str(data.get("package_id") or "").strip()
            owner_id = int(data.get("recipient_user_id") or 0)
            if status == "claimed":
                if owner_id == recipient_id:
                    return GiftClaimResult(True, "already_claimed", "Этот подарок уже закреплён за вашим профилем.", package_id)
                return GiftClaimResult(False, "claimed_by_other", "Этот подарок уже был активирован другим профилем.", package_id)
            if status == "claiming":
                if owner_id == recipient_id:
                    return data
                return GiftClaimResult(False, "claim_in_progress", "Этот подарок уже активируется другим профилем.", package_id)
            if status != "paid":
                return GiftClaimResult(False, status or "not_ready", "Подарок ещё не готов к активации.", package_id)

            updated = conn.execute(
                """
                UPDATE gift_claims
                SET recipient_user_id=?, status='claiming'
                WHERE gift_token=? AND status='paid'
                """.strip(),
                (recipient_id, token),
            )
            if int(getattr(updated, "rowcount", 0) or 0) != 1:
                return GiftClaimResult(False, "claim_race", "Подарок уже активируется. Обновите статус профиля.", package_id)
            data["recipient_user_id"] = recipient_id
            data["status"] = "claiming"
            return data


def claim_gift_token(*, gift_token: str, recipient_user_id: int, platform: str) -> GiftClaimResult:
    token = normalize_gift_token(gift_token)
    if not is_gift_token(token):
        return GiftClaimResult(False, "invalid", "Подарочная ссылка повреждена или неполная.")

    acquired = _acquire_gift_claim(token=token, recipient_user_id=int(recipient_user_id))
    if isinstance(acquired, GiftClaimResult):
        return acquired

    package_id = str(acquired.get("package_id") or "").strip()
    try:
        package = package_by_id(package_id)
        inserted, wallet, _ledger_id = grant_tokens_for_payment(
            provider="gift_claim",
            provider_payment_id=token,
            user_id=int(recipient_user_id),
            package_id=package.package_id,
            source="gift_claim",
        )
        premium = grant_premium_entitlements_for_payment(
            provider="gift_claim",
            provider_payment_id=token,
            user_id=int(recipient_user_id),
            package_id=package.package_id,
            source="gift_claim",
            fallback_platform=platform,
        )
    except (RuntimeError, ValueError) as exc:
        # Keep the claim pinned to this recipient. A retry by the same profile is
        # safe because both token and premium grants are provider-idempotent. Reopening
        # the gift here could grant it to a second user after a partial first grant.
        return GiftClaimResult(False, "grant_failed", f"Не удалось активировать подарок: {type(exc).__name__}", package_id)

    with db() as conn:
        with tx(conn):
            updated = conn.execute(
                """
                UPDATE gift_claims
                SET status='claimed', claimed_at=COALESCE(claimed_at, CURRENT_TIMESTAMP)
                WHERE gift_token=? AND status='claiming' AND recipient_user_id=?
                """.strip(),
                (token, int(recipient_user_id)),
            )
            if int(getattr(updated, "rowcount", 0) or 0) != 1:
                current = _gift_row_in_conn(conn, token)
                if str(current.get("status") or "") == "claimed" and int(current.get("recipient_user_id") or 0) == int(recipient_user_id):
                    return GiftClaimResult(True, "already_claimed", "Этот подарок уже закреплён за вашим профилем.", package_id)
                return GiftClaimResult(False, "claim_race", "Подарок уже активирован. Обновите статус профиля.", package_id)

    premium_tail = ""
    if premium.outbox_created or premium.consultation_request_created:
        premium_tail = " Премиальные материалы/заявка также поставлены в очередь доставки."
    if inserted:
        return GiftClaimResult(
            True,
            "claimed",
            f"Подарок активирован: {package.title}. На балансе теперь {wallet.available_tokens} практик.{premium_tail}",
            package.package_id,
        )
    return GiftClaimResult(True, "already_granted", f"Подарок уже был начислен: {package.title}.", package.package_id)
