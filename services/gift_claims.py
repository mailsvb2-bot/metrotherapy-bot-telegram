from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from services.db import db, tx
from services.practice_token_contract import package_by_id
from services.practice_tokens import grant_tokens_for_payment
from services.premium_entitlements import grant_premium_entitlements_for_payment

_GIFT_TOKEN_RE = re.compile(r"^gift_[a-f0-9]{32}$")


@dataclass(frozen=True)
class GiftClaimResult:
    ok: bool
    status: str
    message: str
    package_id: str = ""


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
                    buyer_user_id=excluded.buyer_user_id,
                    package_id=excluded.package_id,
                    provider=excluded.provider,
                    provider_payment_id=excluded.provider_payment_id,
                    source_platform=excluded.source_platform,
                    status=CASE
                        WHEN gift_claims.status='claimed' THEN gift_claims.status
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


def claim_gift_token(*, gift_token: str, recipient_user_id: int, platform: str) -> GiftClaimResult:
    token = normalize_gift_token(gift_token)
    if not is_gift_token(token):
        return GiftClaimResult(False, "invalid", "Подарочная ссылка повреждена или неполная.")

    with db() as conn:
        row = conn.execute(
            """
            SELECT gift_token, buyer_user_id, recipient_user_id, package_id, provider,
                   provider_payment_id, source_platform, status
            FROM gift_claims
            WHERE gift_token=?
            """.strip(),
            (token,),
        ).fetchone()
    data = _row_to_dict(row)
    if not data:
        return GiftClaimResult(False, "not_paid", "Подарок пока не найден как оплаченный. Проверьте ссылку после успешной оплаты.")

    status = str(data.get("status") or "").strip()
    package_id = str(data.get("package_id") or "").strip()
    if status == "claimed":
        if int(data.get("recipient_user_id") or 0) == int(recipient_user_id):
            return GiftClaimResult(True, "already_claimed", "Этот подарок уже закреплён за вашим профилем.", package_id)
        return GiftClaimResult(False, "claimed_by_other", "Этот подарок уже был активирован другим профилем.", package_id)
    if status != "paid":
        return GiftClaimResult(False, status or "not_ready", "Подарок ещё не готов к активации.", package_id)

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
        return GiftClaimResult(False, "grant_failed", f"Не удалось активировать подарок: {type(exc).__name__}", package_id)

    with db() as conn:
        with tx(conn):
            updated = conn.execute(
                """
                UPDATE gift_claims
                SET recipient_user_id=?, status='claimed', claimed_at=CURRENT_TIMESTAMP
                WHERE gift_token=? AND status='paid'
                """.strip(),
                (int(recipient_user_id), token),
            )
            if getattr(updated, "rowcount", 0) == 0:
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
