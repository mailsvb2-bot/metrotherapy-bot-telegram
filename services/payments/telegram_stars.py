from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from aiogram.types import LabeledPrice, Message

from core.time_utils import utc_now
from services.db import db, tx
from services.events import log_event
from services.gift_claims import (
    create_gift_checkout_token,
    is_gift_token,
    mark_gift_paid,
    normalize_gift_token,
)
from services.practice_token_contract import (
    PracticePackage,
    package_by_id,
    telegram_stars_enabled,
    telegram_stars_price,
)
from services.practice_tokens import grant_tokens_for_payment
from services.premium_entitlements import grant_premium_entitlements_for_payment

log = logging.getLogger(__name__)

STARS_CURRENCY = "XTR"
STARS_PROVIDER = "telegram_stars"
_PAYLOAD_PREFIX = "xtr:v1"


class StarsPaymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class StarsOrder:
    buyer_user_id: int
    package_id: str
    amount_xtr: int
    gift_token: str = ""

    @property
    def is_gift(self) -> bool:
        return bool(self.gift_token)


@dataclass(frozen=True)
class StarsPaymentResult:
    completed: bool
    duplicate: bool
    package_id: str
    gift_token: str = ""
    wallet_balance: int | None = None
    premium_outbox_created: int = 0
    consultation_request_created: bool = False


def _utc_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def build_stars_payload(*, buyer_user_id: int, package_id: str, gift_token: str = "") -> str:
    buyer_id = int(buyer_user_id)
    if buyer_id <= 0:
        raise ValueError("stars_buyer_user_id_required")
    package = package_by_id(package_id)
    if not package.public:
        raise ValueError("stars_package_not_public")
    amount_xtr = telegram_stars_price(package.package_id)

    token = normalize_gift_token(gift_token)
    if token and not is_gift_token(token):
        raise ValueError("stars_gift_token_invalid")
    kind = "g" if token else "p"
    parts = [_PAYLOAD_PREFIX, kind, str(buyer_id), package.package_id, str(amount_xtr)]
    if token:
        parts.append(token)
    payload = ":".join(parts)
    if len(payload.encode("utf-8")) > 128:
        raise ValueError("stars_invoice_payload_too_long")
    return payload


def parse_stars_payload(payload: str | None) -> StarsOrder:
    raw = str(payload or "").strip()
    parts = raw.split(":")
    if len(parts) not in {6, 7} or parts[0:2] != ["xtr", "v1"]:
        raise ValueError("stars_invoice_payload_invalid")
    kind = parts[2]
    if kind not in {"p", "g"}:
        raise ValueError("stars_invoice_kind_invalid")
    try:
        buyer_user_id = int(parts[3])
        amount_xtr = int(parts[5])
    except (TypeError, ValueError) as exc:
        raise ValueError("stars_invoice_numeric_field_invalid") from exc
    if buyer_user_id <= 0:
        raise ValueError("stars_buyer_user_id_invalid")
    if amount_xtr <= 0 or amount_xtr > 100_000:
        raise ValueError("stars_amount_invalid")

    package = package_by_id(parts[4])
    if not package.public:
        raise ValueError("stars_package_not_public")
    token = normalize_gift_token(parts[6] if len(parts) == 7 else "")
    if kind == "g":
        if not token or not is_gift_token(token):
            raise ValueError("stars_gift_token_invalid")
    elif token or len(parts) != 6:
        raise ValueError("stars_invoice_payload_invalid")
    return StarsOrder(
        buyer_user_id=buyer_user_id,
        package_id=package.package_id,
        amount_xtr=amount_xtr,
        gift_token=token,
    )


def _gift_claim_problem(order: StarsOrder, *, pre_checkout: bool) -> str:
    if not order.is_gift:
        return ""
    with db() as conn:
        row = conn.execute(
            """
            SELECT buyer_user_id, package_id, status
            FROM gift_claims
            WHERE gift_token=?
            LIMIT 1
            """.strip(),
            (order.gift_token,),
        ).fetchone()
    if row is None:
        return "stars_gift_not_found"
    buyer = int(row["buyer_user_id"] if hasattr(row, "keys") else row[0])
    package_id = str(row["package_id"] if hasattr(row, "keys") else row[1])
    status = str(row["status"] if hasattr(row, "keys") else row[2])
    if buyer != order.buyer_user_id:
        return "stars_gift_buyer_mismatch"
    if package_id != order.package_id:
        return "stars_gift_package_mismatch"
    allowed_statuses = {"created"} if pre_checkout else {"created", "paid", "claimed"}
    if status not in allowed_statuses:
        return "stars_gift_not_payable"
    return ""


def validate_stars_order(
    *,
    payload: str | None,
    user_id: int,
    currency: str | None,
    total_amount: int | None,
    pre_checkout: bool,
) -> StarsOrder:
    # The feature flag controls creation and pre-checkout of *new* invoices.
    # A successful_payment update is proof that Telegram has already charged the
    # user, so it must remain processable after a rollout flag/config change.
    if pre_checkout and not telegram_stars_enabled():
        raise ValueError("stars_payments_disabled")
    if str(currency or "").strip().upper() != STARS_CURRENCY:
        raise ValueError("stars_currency_invalid")
    order = parse_stars_payload(payload)
    if int(user_id) != order.buyer_user_id:
        raise ValueError("stars_buyer_mismatch")
    try:
        amount = int(total_amount or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("stars_amount_invalid") from exc
    if amount != order.amount_xtr:
        raise ValueError("stars_amount_mismatch")
    if pre_checkout and telegram_stars_price(order.package_id) != order.amount_xtr:
        raise ValueError("stars_invoice_price_stale")
    gift_problem = _gift_claim_problem(order, pre_checkout=pre_checkout)
    if gift_problem:
        raise ValueError(gift_problem)
    return order


def validate_stars_pre_checkout(
    *,
    payload: str | None,
    user_id: int,
    currency: str | None,
    total_amount: int | None,
) -> str | None:
    try:
        validate_stars_order(
            payload=payload,
            user_id=int(user_id),
            currency=currency,
            total_amount=total_amount,
            pre_checkout=True,
        )
    except ValueError as exc:
        log.warning("Telegram Stars pre-checkout rejected: user_id=%s reason=%s", user_id, exc)
        return "Не удалось проверить покупку. Обновите список пакетов и попробуйте снова."
    return None


async def send_stars_invoice(
    message: Message,
    *,
    package_id: str,
    as_gift: bool = False,
) -> str:
    if not telegram_stars_enabled():
        raise StarsPaymentError("stars_payments_disabled")
    user = message.from_user
    if user is None:
        raise StarsPaymentError("stars_buyer_missing")
    package: PracticePackage = package_by_id(package_id)
    if not package.public:
        raise StarsPaymentError("stars_package_not_public")

    gift_token = ""
    if as_gift:
        gift_token = create_gift_checkout_token(
            buyer_user_id=int(user.id),
            package_id=package.package_id,
            source_platform="telegram",
        )
    payload = build_stars_payload(
        buyer_user_id=int(user.id),
        package_id=package.package_id,
        gift_token=gift_token,
    )
    order = parse_stars_payload(payload)
    description = package.description
    if as_gift:
        description = f"Подарок: {package.description} Получатель активирует пакет по универсальной ссылке."

    await message.answer_invoice(
        title=package.title[:32],
        description=description[:255],
        payload=payload,
        provider_token="",
        currency=STARS_CURRENCY,
        prices=[LabeledPrice(label=package.title[:32], amount=order.amount_xtr)],
        start_parameter=f"xtr_{package.package_id}"[:64],
    )
    log_event(
        int(user.id),
        "telegram_stars_invoice_created",
        {
            "package_id": package.package_id,
            "gift": bool(as_gift),
            "amount_xtr": order.amount_xtr,
        },
    )
    return gift_token


def _payment_row(charge_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT user_id, telegram_charge_id, payload, amount, currency,
                   processing_status, side_effects_done_at_utc
            FROM payments
            WHERE telegram_charge_id=?
            LIMIT 1
            """.strip(),
            (charge_id,),
        ).fetchone()
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {
        "user_id": row[0],
        "telegram_charge_id": row[1],
        "payload": row[2],
        "amount": row[3],
        "currency": row[4],
        "processing_status": row[5],
        "side_effects_done_at_utc": row[6],
    }


def _record_received_payment_fact(
    *,
    user_id: int,
    charge_id: str,
    provider_charge_id: str,
    payload: str,
    amount: int,
    currency: str,
) -> None:
    """Persist Telegram's successful-payment fact before business validation.

    Telegram has already charged the buyer when this function is reached. Even
    a stale/unknown payload must therefore be durable and visible to support and
    reconciliation instead of disappearing before validation.
    """
    now = _utc_iso()
    normalized_currency = str(currency or "").strip().upper()
    raw = json.dumps(
        {
            "provider": STARS_PROVIDER,
            "received": True,
            "amount": int(amount),
            "currency": normalized_currency,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                INSERT OR IGNORE INTO payments(
                    user_id, telegram_charge_id, provider_charge_id, payload,
                    amount, currency, created_at, provider_status,
                    provider_event_id, provider_raw, reconciled_at, problem,
                    processing_status, processing_error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """.strip(),
                (
                    int(user_id),
                    charge_id,
                    provider_charge_id or None,
                    payload,
                    int(amount),
                    normalized_currency,
                    now,
                    "succeeded",
                    charge_id,
                    raw,
                    now,
                    "",
                    "grant_pending",
                    "",
                ),
            )

    row = _payment_row(charge_id)
    if not row:
        raise StarsPaymentError("stars_payment_fact_missing")
    if int(row.get("user_id") or 0) != int(user_id):
        raise StarsPaymentError("stars_payment_user_conflict")
    if str(row.get("payload") or "") != payload:
        raise StarsPaymentError("stars_payment_payload_conflict")
    if int(row.get("amount") or 0) != int(amount):
        raise StarsPaymentError("stars_payment_amount_conflict")
    if str(row.get("currency") or "").upper() != normalized_currency:
        raise StarsPaymentError("stars_payment_currency_conflict")


def _record_validated_payment_fact(
    *,
    order: StarsOrder,
    charge_id: str,
    provider_charge_id: str,
    payload: str,
    amount: int,
) -> None:
    now = _utc_iso()
    raw = json.dumps(
        {
            "provider": STARS_PROVIDER,
            "package_id": order.package_id,
            "gift": order.is_gift,
            "amount_xtr": order.amount_xtr,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                UPDATE payments
                SET provider_charge_id=COALESCE(provider_charge_id, ?),
                    provider_status='succeeded', provider_event_id=?, provider_raw=?,
                    reconciled_at=?, problem='', processing_error='',
                    processing_status=CASE
                        WHEN side_effects_done_at_utc IS NOT NULL THEN processing_status
                        ELSE 'grant_pending'
                    END
                WHERE telegram_charge_id=?
                """.strip(),
                (provider_charge_id or None, charge_id, raw, now, charge_id),
            )
    row = _payment_row(charge_id)
    if not row:
        raise StarsPaymentError("stars_payment_fact_missing")
    if int(row.get("user_id") or 0) != order.buyer_user_id:
        raise StarsPaymentError("stars_payment_user_conflict")
    if str(row.get("payload") or "") != payload:
        raise StarsPaymentError("stars_payment_payload_conflict")
    if int(row.get("amount") or 0) != int(amount):
        raise StarsPaymentError("stars_payment_amount_conflict")
    if str(row.get("currency") or "").upper() != STARS_CURRENCY:
        raise StarsPaymentError("stars_payment_currency_conflict")


def _mark_payment_done(charge_id: str) -> None:
    now = _utc_iso()
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                UPDATE payments
                SET processing_status='side_effects_done',
                    granted_at_utc=COALESCE(granted_at_utc, ?),
                    side_effects_done_at_utc=COALESCE(side_effects_done_at_utc, ?),
                    processing_error='', problem='', provider_status='succeeded',
                    reconciled_at=?
                WHERE telegram_charge_id=?
                """.strip(),
                (now, now, now, charge_id),
            )


def _mark_payment_error(charge_id: str, exc: BaseException) -> None:
    message = f"{type(exc).__name__}:{str(exc)}"[:500]
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                UPDATE payments
                SET processing_status='action_required', processing_error=?, problem=?
                WHERE telegram_charge_id=? AND side_effects_done_at_utc IS NULL
                """.strip(),
                (message, message, charge_id),
            )


def record_successful_stars_payment(
    *,
    user_id: int,
    payload: str,
    total_amount: int,
    currency: str,
    telegram_charge_id: str,
    provider_charge_id: str = "",
) -> StarsPaymentResult:
    charge_id = str(telegram_charge_id or "").strip()
    if not charge_id:
        raise StarsPaymentError("stars_charge_id_missing")
    normalized_payload = str(payload or "")
    normalized_provider_charge = str(provider_charge_id or "").strip()
    try:
        normalized_amount = int(total_amount)
    except (TypeError, ValueError) as exc:
        raise StarsPaymentError("stars_amount_invalid") from exc

    _record_received_payment_fact(
        user_id=int(user_id),
        charge_id=charge_id,
        provider_charge_id=normalized_provider_charge,
        payload=normalized_payload,
        amount=normalized_amount,
        currency=str(currency or ""),
    )
    try:
        order = validate_stars_order(
            payload=normalized_payload,
            user_id=int(user_id),
            currency=currency,
            total_amount=normalized_amount,
            pre_checkout=False,
        )
    except ValueError as exc:
        _mark_payment_error(charge_id, exc)
        raise StarsPaymentError("stars_successful_payment_validation_failed") from exc

    _record_validated_payment_fact(
        order=order,
        charge_id=charge_id,
        provider_charge_id=normalized_provider_charge,
        payload=normalized_payload,
        amount=normalized_amount,
    )
    existing = _payment_row(charge_id)
    if existing.get("side_effects_done_at_utc"):
        return StarsPaymentResult(
            completed=True,
            duplicate=True,
            package_id=order.package_id,
            gift_token=order.gift_token,
        )

    try:
        if order.is_gift:
            mark_gift_paid(
                gift_token=order.gift_token,
                buyer_user_id=order.buyer_user_id,
                package_id=order.package_id,
                provider=STARS_PROVIDER,
                provider_payment_id=charge_id,
                source_platform="telegram",
            )
            wallet_balance = None
            premium_outbox = 0
            consultation_created = False
        else:
            _inserted, wallet, _ledger_id = grant_tokens_for_payment(
                provider=STARS_PROVIDER,
                provider_payment_id=charge_id,
                user_id=order.buyer_user_id,
                package_id=order.package_id,
                source="telegram_successful_payment",
            )
            premium = grant_premium_entitlements_for_payment(
                provider=STARS_PROVIDER,
                provider_payment_id=charge_id,
                user_id=order.buyer_user_id,
                package_id=order.package_id,
                source="telegram_successful_payment",
                fallback_platform="telegram",
            )
            wallet_balance = int(wallet.available_tokens)
            premium_outbox = int(premium.outbox_created)
            consultation_created = bool(premium.consultation_request_created)
        _mark_payment_done(charge_id)
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        _mark_payment_error(charge_id, exc)
        log.exception("Telegram Stars post-payment processing failed: charge_id=%s", charge_id)
        raise StarsPaymentError("stars_post_payment_processing_failed") from exc

    log_event(
        order.buyer_user_id,
        "telegram_stars_paid",
        {
            "package_id": order.package_id,
            "gift": order.is_gift,
            "amount_xtr": normalized_amount,
            "charge_id": charge_id,
        },
    )
    return StarsPaymentResult(
        completed=True,
        duplicate=False,
        package_id=order.package_id,
        gift_token=order.gift_token,
        wallet_balance=wallet_balance,
        premium_outbox_created=premium_outbox,
        consultation_request_created=consultation_created,
    )
