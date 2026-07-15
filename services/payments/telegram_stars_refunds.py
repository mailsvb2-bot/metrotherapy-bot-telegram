from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.time_utils import utc_now
from services.db import db, tx
from services.payments.telegram_stars import STARS_CURRENCY, STARS_PROVIDER, parse_stars_payload
from services.practice_tokens_wallet import get_wallet_in_conn, insert_ledger


class StarsRefundError(RuntimeError):
    pass


@dataclass(frozen=True)
class StarsRefundPlan:
    telegram_charge_id: str
    payment_user_id: int = 0
    beneficiary_user_id: int = 0
    package_id: str = ""
    gift_token: str = ""
    tokens: int = 0
    status: str = "new"
    attempt: int = 0
    refundable: bool = False
    reason: str = ""

    @property
    def is_gift(self) -> bool:
        return bool(self.gift_token)


def _utc_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def _dict(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    raise TypeError("stars_refund_row_mapping_required")


def _refund_state(charge_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM telegram_stars_refunds WHERE telegram_charge_id=? LIMIT 1",
            (charge_id,),
        ).fetchone()
    return _dict(row)


def _state_plan(state: dict[str, Any]) -> StarsRefundPlan:
    status = str(state.get("status") or "new")
    return StarsRefundPlan(
        telegram_charge_id=str(state.get("telegram_charge_id") or ""),
        payment_user_id=int(state.get("payment_user_id") or 0),
        beneficiary_user_id=int(state.get("beneficiary_user_id") or 0),
        package_id=str(state.get("package_id") or ""),
        gift_token=str(state.get("gift_token") or ""),
        tokens=int(state.get("tokens_held") or 0),
        status=status,
        attempt=int(state.get("attempts") or 0),
        refundable=status in {"prepared", "provider_refunded"},
        reason="already_refunded" if status == "completed" else "resume_refund",
    )


def _delivery_pattern(charge_id: str) -> str:
    escaped = str(charge_id).replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"premium_delivery:{STARS_PROVIDER}:{escaped}:%"


def _premium_refund_problem(*, user_id: int, charge_id: str) -> str:
    prefix = _delivery_pattern(charge_id)
    with db() as conn:
        delivered = conn.execute(
            """
            SELECT status
            FROM premium_delivery_outbox
            WHERE user_id=? AND idempotency_key LIKE ? ESCAPE '!'
              AND status NOT IN ('pending', 'refund_pending', 'cancelled')
            LIMIT 1
            """.strip(),
            (int(user_id), prefix),
        ).fetchone()
        consultation = conn.execute(
            """
            SELECT status
            FROM consultation_requests
            WHERE user_id=? AND provider=? AND provider_payment_id=?
              AND status NOT IN ('new', 'refund_pending', 'cancelled')
            LIMIT 1
            """.strip(),
            (int(user_id), STARS_PROVIDER, charge_id),
        ).fetchone()
    if delivered:
        return "premium_content_already_delivered"
    if consultation:
        return "consultation_already_in_progress"
    return ""


def _payment_record(charge_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT user_id, payload, amount, currency, provider_status,
                   processing_status, side_effects_done_at_utc
            FROM payments
            WHERE telegram_charge_id=?
            LIMIT 1
            """.strip(),
            (charge_id,),
        ).fetchone()
    return _dict(row)


def _gift_refund_plan(
    *,
    charge_id: str,
    payment_user_id: int,
    package_id: str,
    gift_token: str,
) -> StarsRefundPlan:
    with db() as conn:
        gift = _dict(
            conn.execute(
                """
            SELECT status, package_id, recipient_user_id
            FROM gift_claims
            WHERE gift_token=? AND provider=? AND provider_payment_id=?
            LIMIT 1
            """.strip(),
                (gift_token, STARS_PROVIDER, charge_id),
            ).fetchone()
        )
    status = str(gift.get("status") or "")
    if status == "paid":
        return StarsRefundPlan(
            charge_id,
            payment_user_id=payment_user_id,
            package_id=package_id,
            gift_token=gift_token,
            refundable=True,
            reason="gift_unclaimed",
        )
    reason = "gift_already_claimed" if status == "claimed" else "gift_not_refundable"
    return StarsRefundPlan(
        charge_id,
        payment_user_id=payment_user_id,
        package_id=package_id,
        gift_token=gift_token,
        reason=reason,
    )


def _token_refund_plan(
    *,
    charge_id: str,
    payment_user_id: int,
    package_id: str,
    processing_status: str,
) -> StarsRefundPlan:
    with db() as conn:
        grant = _dict(
            conn.execute(
                """
            SELECT user_id, package_id, tokens_granted
            FROM payment_token_grants
            WHERE provider=? AND provider_payment_id=?
            LIMIT 1
            """.strip(),
                (STARS_PROVIDER, charge_id),
            ).fetchone()
        )

    if not grant:
        return StarsRefundPlan(
            charge_id,
            payment_user_id=payment_user_id,
            package_id=package_id,
            refundable=processing_status == "action_required",
            reason=(
                "charged_without_entitlement"
                if processing_status == "action_required"
                else "payment_entitlement_not_settled"
            ),
        )

    beneficiary_user_id = int(grant.get("user_id") or 0)
    tokens = int(grant.get("tokens_granted") or 0)
    package_id = str(grant.get("package_id") or package_id)
    with db() as conn:
        wallet = _dict(
            conn.execute(
                "SELECT available_tokens FROM practice_wallets WHERE user_id=? LIMIT 1",
                (beneficiary_user_id,),
            ).fetchone()
        )
    if int(wallet.get("available_tokens") or 0) < tokens:
        return StarsRefundPlan(
            charge_id,
            payment_user_id=payment_user_id,
            beneficiary_user_id=beneficiary_user_id,
            package_id=package_id,
            tokens=tokens,
            reason="purchased_practices_already_used_or_reserved",
        )

    premium_problem = _premium_refund_problem(user_id=beneficiary_user_id, charge_id=charge_id)
    return StarsRefundPlan(
        charge_id,
        payment_user_id=payment_user_id,
        beneficiary_user_id=beneficiary_user_id,
        package_id=package_id,
        tokens=tokens,
        refundable=not premium_problem,
        reason=premium_problem or "ready",
    )


def preview_stars_refund(telegram_charge_id: str) -> StarsRefundPlan:
    charge_id = str(telegram_charge_id or "").strip()
    if not charge_id:
        raise StarsRefundError("telegram_charge_id_required")

    state = _refund_state(charge_id)
    if state and str(state.get("status") or "") in {"prepared", "provider_refunded", "completed"}:
        return _state_plan(state)

    payment = _payment_record(charge_id)
    if not payment:
        return StarsRefundPlan(charge_id, reason="payment_not_found")

    payment_user_id = int(payment.get("user_id") or 0)
    if str(payment.get("currency") or "").upper() != STARS_CURRENCY:
        return StarsRefundPlan(charge_id, payment_user_id=payment_user_id, reason="not_a_stars_payment")
    if str(payment.get("provider_status") or "").lower() == "refunded":
        return StarsRefundPlan(charge_id, payment_user_id=payment_user_id, reason="provider_already_refunded")

    order = None
    try:
        order = parse_stars_payload(str(payment.get("payload") or ""))
    except ValueError:
        order = None

    gift_token = order.gift_token if order is not None else ""
    package_id = order.package_id if order is not None else ""
    if gift_token:
        return _gift_refund_plan(
            charge_id=charge_id,
            payment_user_id=payment_user_id,
            package_id=package_id,
            gift_token=gift_token,
        )
    return _token_refund_plan(
        charge_id=charge_id,
        payment_user_id=payment_user_id,
        package_id=package_id,
        processing_status=str(payment.get("processing_status") or ""),
    )


def prepare_stars_refund(telegram_charge_id: str, *, requested_by: int) -> StarsRefundPlan:
    plan = preview_stars_refund(telegram_charge_id)
    if plan.status in {"prepared", "provider_refunded", "completed"}:
        return plan
    if not plan.refundable:
        raise StarsRefundError(plan.reason or "refund_not_allowed")

    charge_id = plan.telegram_charge_id
    now = _utc_iso()
    prefix = _delivery_pattern(charge_id)

    with db() as conn:
        with tx(conn):
            existing = _dict(
                conn.execute(
                    "SELECT status, attempts FROM telegram_stars_refunds WHERE telegram_charge_id=?",
                    (charge_id,),
                ).fetchone()
            )
            existing_status = str(existing.get("status") or "")
            if existing_status and existing_status != "failed":
                raise StarsRefundError("refund_already_in_progress")
            attempt = int(existing.get("attempts") or 0) + 1
            if existing:
                claimed = conn.execute(
                    """
                    UPDATE telegram_stars_refunds
                    SET payment_user_id=?, beneficiary_user_id=?, package_id=?, gift_token=?,
                        tokens_held=0, status='preparing', attempts=?, requested_by=?,
                        last_error='', updated_at=?
                    WHERE telegram_charge_id=? AND status='failed'
                    """.strip(),
                    (
                        plan.payment_user_id,
                        plan.beneficiary_user_id or None,
                        plan.package_id,
                        plan.gift_token,
                        attempt,
                        int(requested_by),
                        now,
                        charge_id,
                    ),
                )
                if int(getattr(claimed, "rowcount", 0) or 0) <= 0:
                    raise StarsRefundError("refund_already_in_progress")
            else:
                conn.execute(
                    """
                    INSERT INTO telegram_stars_refunds(
                        telegram_charge_id, payment_user_id, beneficiary_user_id,
                        package_id, gift_token, tokens_held, status, attempts,
                        requested_by, last_error, created_at, updated_at
                    ) VALUES(?,?,?,?,?,0,'preparing',?,?, '',?,?)
                    """.strip(),
                    (
                        charge_id,
                        plan.payment_user_id,
                        plan.beneficiary_user_id or None,
                        plan.package_id,
                        plan.gift_token,
                        attempt,
                        int(requested_by),
                        now,
                        now,
                    ),
                )

            if plan.tokens:
                cursor = conn.execute(
                    """
                    UPDATE practice_wallets
                    SET available_tokens=available_tokens-?, updated_at=CURRENT_TIMESTAMP
                    WHERE user_id=? AND available_tokens>=?
                    """.strip(),
                    (plan.tokens, plan.beneficiary_user_id, plan.tokens),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
                    raise StarsRefundError("refund_token_hold_failed")
                wallet = get_wallet_in_conn(conn, plan.beneficiary_user_id)
                insert_ledger(
                    conn,
                    user_id=plan.beneficiary_user_id,
                    event_type="refund_hold",
                    amount=-plan.tokens,
                    balance_after=wallet.available_tokens,
                    reason="telegram_stars_refund_requested",
                    source="admin_refund",
                    package_id=plan.package_id,
                    provider=STARS_PROVIDER,
                    provider_payment_id=charge_id,
                    idempotency_key=f"stars_refund_hold:{charge_id}:{attempt}",
                )

            conn.execute(
                """
                UPDATE premium_entitlements
                SET status='refund_pending', updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND provider=? AND provider_payment_id=? AND status='active'
                """.strip(),
                (plan.beneficiary_user_id, STARS_PROVIDER, charge_id),
            )
            conn.execute(
                """
                UPDATE premium_delivery_outbox
                SET status='refund_pending', updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND idempotency_key LIKE ? ESCAPE '!' AND status='pending'
                """.strip(),
                (plan.beneficiary_user_id, prefix),
            )
            conn.execute(
                """
                UPDATE consultation_requests
                SET status='refund_pending', updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND provider=? AND provider_payment_id=? AND status='new'
                """.strip(),
                (plan.beneficiary_user_id, STARS_PROVIDER, charge_id),
            )
            if plan.gift_token:
                cursor = conn.execute(
                    "UPDATE gift_claims SET status='refund_pending' WHERE gift_token=? AND status='paid'",
                    (plan.gift_token,),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
                    raise StarsRefundError("gift_refund_hold_failed")

            conn.execute(
                """
                UPDATE telegram_stars_refunds
                SET tokens_held=?, status='prepared', updated_at=?
                WHERE telegram_charge_id=? AND status='preparing'
                """.strip(),
                (plan.tokens, now, charge_id),
            )
    return preview_stars_refund(charge_id)


def cancel_prepared_stars_refund(telegram_charge_id: str, *, error: str) -> StarsRefundPlan:
    charge_id = str(telegram_charge_id or "").strip()
    state = _refund_state(charge_id)
    if not state or str(state.get("status") or "") != "prepared":
        return preview_stars_refund(charge_id)

    user_id = int(state.get("beneficiary_user_id") or 0)
    tokens = int(state.get("tokens_held") or 0)
    attempt = int(state.get("attempts") or 0)
    gift_token = str(state.get("gift_token") or "")
    prefix = _delivery_pattern(charge_id)
    with db() as conn:
        with tx(conn):
            if tokens and user_id:
                conn.execute(
                    """
                    UPDATE practice_wallets
                    SET available_tokens=available_tokens+?, updated_at=CURRENT_TIMESTAMP
                    WHERE user_id=?
                    """.strip(),
                    (tokens, user_id),
                )
                wallet = get_wallet_in_conn(conn, user_id)
                insert_ledger(
                    conn,
                    user_id=user_id,
                    event_type="refund_release",
                    amount=tokens,
                    balance_after=wallet.available_tokens,
                    reason="telegram_stars_refund_provider_failed",
                    source="admin_refund",
                    package_id=str(state.get("package_id") or ""),
                    provider=STARS_PROVIDER,
                    provider_payment_id=charge_id,
                    idempotency_key=f"stars_refund_release:{charge_id}:{attempt}",
                )
            conn.execute(
                "UPDATE premium_entitlements SET status='active', updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND provider=? AND provider_payment_id=? AND status='refund_pending'",
                (user_id, STARS_PROVIDER, charge_id),
            )
            conn.execute(
                "UPDATE premium_delivery_outbox SET status='pending', updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND idempotency_key LIKE ? ESCAPE '!' AND status='refund_pending'",
                (user_id, prefix),
            )
            conn.execute(
                "UPDATE consultation_requests SET status='new', updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND provider=? AND provider_payment_id=? AND status='refund_pending'",
                (user_id, STARS_PROVIDER, charge_id),
            )
            if gift_token:
                conn.execute(
                    "UPDATE gift_claims SET status='paid' WHERE gift_token=? AND status='refund_pending'",
                    (gift_token,),
                )
            conn.execute(
                """
                UPDATE telegram_stars_refunds
                SET tokens_held=0, status='failed', last_error=?, updated_at=?
                WHERE telegram_charge_id=? AND status='prepared'
                """.strip(),
                (str(error or "provider_refund_failed")[:500], _utc_iso(), charge_id),
            )
    return preview_stars_refund(charge_id)


def mark_stars_refund_provider_succeeded(telegram_charge_id: str) -> StarsRefundPlan:
    charge_id = str(telegram_charge_id or "").strip()
    now = _utc_iso()
    with db() as conn:
        with tx(conn):
            cursor = conn.execute(
                """
                UPDATE telegram_stars_refunds
                SET status='provider_refunded', provider_refunded_at=COALESCE(provider_refunded_at, ?),
                    last_error='', updated_at=?
                WHERE telegram_charge_id=? AND status IN ('prepared', 'provider_refunded')
                """.strip(),
                (now, now, charge_id),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
                state = _dict(
                    conn.execute(
                        "SELECT status FROM telegram_stars_refunds WHERE telegram_charge_id=?",
                        (charge_id,),
                    ).fetchone()
                )
                if str(state.get("status") or "") != "completed":
                    raise StarsRefundError("refund_not_prepared")
    return preview_stars_refund(charge_id)


def complete_stars_refund(telegram_charge_id: str) -> StarsRefundPlan:
    charge_id = str(telegram_charge_id or "").strip()
    state = _refund_state(charge_id)
    if str(state.get("status") or "") == "completed":
        return _state_plan(state)
    if str(state.get("status") or "") != "provider_refunded":
        raise StarsRefundError("provider_refund_not_confirmed")

    user_id = int(state.get("beneficiary_user_id") or 0)
    tokens = int(state.get("tokens_held") or 0)
    attempt = int(state.get("attempts") or 0)
    gift_token = str(state.get("gift_token") or "")
    prefix = _delivery_pattern(charge_id)
    now = _utc_iso()
    with db() as conn:
        with tx(conn):
            if tokens and user_id:
                conn.execute(
                    """
                    UPDATE practice_wallets
                    SET refunded_tokens=refunded_tokens+?, updated_at=CURRENT_TIMESTAMP
                    WHERE user_id=?
                    """.strip(),
                    (tokens, user_id),
                )
                wallet = get_wallet_in_conn(conn, user_id)
                insert_ledger(
                    conn,
                    user_id=user_id,
                    event_type="refund_finalize",
                    amount=0,
                    balance_after=wallet.available_tokens,
                    reason="telegram_stars_refunded",
                    source="admin_refund",
                    package_id=str(state.get("package_id") or ""),
                    provider=STARS_PROVIDER,
                    provider_payment_id=charge_id,
                    idempotency_key=f"stars_refund_finalize:{charge_id}:{attempt}",
                )
            conn.execute(
                "UPDATE premium_entitlements SET status='revoked', updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND provider=? AND provider_payment_id=? AND status='refund_pending'",
                (user_id, STARS_PROVIDER, charge_id),
            )
            conn.execute(
                "UPDATE premium_delivery_outbox SET status='cancelled', updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND idempotency_key LIKE ? ESCAPE '!' AND status='refund_pending'",
                (user_id, prefix),
            )
            conn.execute(
                "UPDATE consultation_requests SET status='cancelled', updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND provider=? AND provider_payment_id=? AND status='refund_pending'",
                (user_id, STARS_PROVIDER, charge_id),
            )
            if gift_token:
                conn.execute(
                    "UPDATE gift_claims SET status='refunded' WHERE gift_token=? AND status='refund_pending'",
                    (gift_token,),
                )
            conn.execute(
                """
                UPDATE payments
                SET provider_status='refunded', processing_status='refunded',
                    processing_error='', problem='', reconciled_at=?
                WHERE telegram_charge_id=?
                """.strip(),
                (now, charge_id),
            )
            conn.execute(
                """
                UPDATE telegram_stars_refunds
                SET status='completed', completed_at=COALESCE(completed_at, ?),
                    last_error='', updated_at=?
                WHERE telegram_charge_id=? AND status='provider_refunded'
                """.strip(),
                (now, now, charge_id),
            )
    return _state_plan(_refund_state(charge_id))


def refund_plan_text(plan: StarsRefundPlan) -> str:
    labels = {
        "ready": "готов к возврату",
        "gift_unclaimed": "подарок не активирован",
        "charged_without_entitlement": "оплата есть, доступ не начислен",
        "resume_refund": "возврат уже начат",
        "already_refunded": "возврат уже завершён",
        "payment_not_found": "платёж не найден",
        "not_a_stars_payment": "это не платёж Stars",
        "provider_already_refunded": "Telegram уже отметил платёж возвращённым",
        "gift_already_claimed": "подарок уже активирован",
        "gift_not_refundable": "подарок нельзя вернуть автоматически",
        "payment_entitlement_not_settled": "начисление платежа ещё не завершено",
        "purchased_practices_already_used_or_reserved": "часть купленных практик уже использована или зарезервирована",
        "premium_content_already_delivered": "премиальный материал уже доставлен",
        "consultation_already_in_progress": "заявка на консультацию уже обрабатывается",
    }
    reason = labels.get(plan.reason, plan.reason or "неизвестно")
    return (
        "Возврат Telegram Stars\n\n"
        f"charge_id: {plan.telegram_charge_id}\n"
        f"покупатель: {plan.payment_user_id or '-'}\n"
        f"пакет: {plan.package_id or '-'}\n"
        f"практик к отзыву: {plan.tokens}\n"
        f"статус: {plan.status}\n"
        f"проверка: {reason}"
    )
