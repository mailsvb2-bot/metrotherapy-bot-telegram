from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, PreCheckoutQuery

from core.time_utils import utc_now
from services.db import db
from services.plan_store import get_plan_id, clear_plan
from services.plans import get_plan_by_id
from services.subscription import grant, grant_tx
from services.jobs import cancel_funnel, add_job, cancel_jobs
from services.events import log_event
from services.referrals import get_referrer, reward_already_given, mark_reward_given, can_reward_referrer
from services.gifts import mark_gift_paid_tx
from services.gift_store import clear_target
from config.settings import settings

from services.payments.ui import kb_after_paid
from services.payments.gift import deliver_gift_message

logger = logging.getLogger(__name__)


def payment_insert_values(
    *,
    user_id: int,
    telegram_charge_id: str,
    provider_charge_id: str | None,
    payload: str,
    amount: int,
    currency: str | None,
    created_at: str,
    decision_id: str | None,
    correlation_id: str | None,
) -> tuple[Any, ...]:
    """Return values in the exact order expected by the payments INSERT."""
    return (
        int(user_id),
        telegram_charge_id,
        provider_charge_id,
        payload,
        int(amount),
        currency,
        created_at,
        decision_id,
        correlation_id,
    )


def _base_payment_payload(payload: str | None) -> str:
    """Strip DecisionCore attribution suffix from Telegram invoice payload."""
    raw = (payload or "").strip()
    if "|" not in raw:
        return raw
    return raw.split("|", 1)[0].strip()


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, TypeError, IndexError):
            pass
    try:
        return row[index]
    except (TypeError, KeyError, IndexError):
        return default


def _expected_minor_amount_from_plan(plan: dict[str, Any] | None) -> int:
    if not plan or not plan.get("is_active"):
        return 0
    try:
        price_rub = int(plan.get("price") or 0)
    except (TypeError, ValueError):
        return 0
    if price_rub >= 50000 and price_rub % 100 == 0:
        price_rub = price_rub // 100
    return int(price_rub) * 100 if price_rub > 0 else 0


def _gift_plan_id_by_code(code: str) -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT plan_id, paid, status FROM gift_codes WHERE code=? LIMIT 1",
            (code,),
        ).fetchone()
    if row is None:
        return 0
    try:
        if int(_row_value(row, "paid", 1, 0) or 0) == 1:
            return 0
    except (TypeError, ValueError):
        return 0
    try:
        return int(_row_value(row, "plan_id", 0, 0) or 0)
    except (TypeError, ValueError):
        return 0


def validate_pre_checkout_invoice(*, payload: str | None, currency: str | None, total_amount: int | None) -> str | None:
    """Return None when Telegram pre-checkout invoice is still valid.

    This is the last synchronous guard before Telegram finalizes a payment.
    It protects against stale invoice buttons after an admin changes/deactivates
    tariffs and against malformed payloads that would otherwise be accepted and
    only fail after money movement.
    """
    if (currency or "").strip().upper() != "RUB":
        return "Платёж отклонён: поддерживается только RUB. Обновите тариф и попробуйте снова."

    try:
        requested_amount = int(total_amount or 0)
    except (TypeError, ValueError):
        return "Платёж отклонён: некорректная сумма. Обновите тариф и попробуйте снова."
    if requested_amount <= 0:
        return "Платёж отклонён: сумма должна быть больше нуля. Обновите тариф и попробуйте снова."

    base_payload = _base_payment_payload(payload)

    plan_id = 0
    if base_payload.startswith("sub:"):
        try:
            plan_id = int(base_payload.split(":", 1)[1].strip() or 0)
        except (TypeError, ValueError):
            plan_id = 0
    elif base_payload.startswith("gift:"):
        code = base_payload.split(":", 1)[1].strip()
        if not code:
            return "Платёж отклонён: подарочный код не найден. Создайте подарок заново."
        plan_id = _gift_plan_id_by_code(code)
        if not plan_id:
            return "Платёж отклонён: подарок устарел или уже оплачен. Создайте подарок заново."
    else:
        return "Платёж отклонён: неизвестный тип платежа. Выберите тариф заново."

    if not plan_id:
        return "Платёж отклонён: тариф не найден. Выберите тариф заново."

    plan = get_plan_by_id(int(plan_id))
    expected_amount = _expected_minor_amount_from_plan(plan)
    if expected_amount <= 0:
        return "Платёж отклонён: тариф недоступен. Выберите тариф заново."
    if requested_amount != expected_amount:
        return "Цена изменилась. Пожалуйста, откройте тарифы и сформируйте платёж заново."
    return None


async def _answer_pre_checkout_temporarily_unavailable(pre: PreCheckoutQuery) -> None:
    try:
        await pre.answer(
            ok=False,
            error_message="Платёж временно недоступен. Попробуйте ещё раз через минуту.",
        )
    except (TelegramAPIError, asyncio.TimeoutError):
        logger.exception("pre_checkout_query negative answer failed")


async def pre_checkout(pre: PreCheckoutQuery) -> None:
    payload = getattr(pre, "invoice_payload", "") or ""
    currency = getattr(pre, "currency", None)
    total_amount = getattr(pre, "total_amount", None)
    try:
        logger.info(
            "pre_checkout_query: uid=%s currency=%s total=%s payload=%s",
            getattr(pre.from_user, "id", None),
            currency,
            total_amount,
            payload[:64],
        )
    except (AttributeError, TypeError, ValueError):
        logger.exception("pre_checkout_query log failed")

    try:
        error_message = await asyncio.to_thread(
            validate_pre_checkout_invoice,
            payload=payload,
            currency=currency,
            total_amount=total_amount,
        )
        if error_message:
            logger.warning(
                "pre_checkout_query rejected: uid=%s reason=%s payload=%s",
                getattr(getattr(pre, "from_user", None), "id", None),
                error_message,
                payload[:64],
            )
            await pre.answer(ok=False, error_message=error_message)
            return
        await pre.answer(ok=True)
    except (TelegramAPIError, asyncio.TimeoutError):
        logger.exception("pre_checkout_query answer failed")
    except (sqlite3.Error, RuntimeError):
        logger.exception("pre_checkout_query validation failed")
        await _answer_pre_checkout_temporarily_unavailable(pre)
    except (ValueError, TypeError):
        logger.exception("pre_checkout_query validation failed")
        await _answer_pre_checkout_temporarily_unavailable(pre)


async def successful_payment(message: Message) -> None:
    sp = message.successful_payment
    payload = (sp.invoice_payload or "").strip()
    decision_id = None
    correlation_id = None
    if '|d=' in payload:
        try:
            parts = payload.split('|')
            base = parts[0]
            for p in parts[1:]:
                if p.startswith('d='):
                    decision_id = p[2:] or None
                if p.startswith('c='):
                    correlation_id = p[2:] or None
            payload = base
        except (AttributeError, TypeError, ValueError):
            decision_id = None
            correlation_id = None
    log_event(message.from_user.id, "invoice_paid", {"payload": payload, "amount": sp.total_amount})

    charge_id = (getattr(sp, "telegram_payment_charge_id", "") or "").strip()
    provider_id = (getattr(sp, "provider_payment_charge_id", "") or "").strip()
    with db() as conn:
        try:
            conn.execute("BEGIN")
            if charge_id:
                created_at = utc_now().replace(tzinfo=None, microsecond=0).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO payments(user_id, telegram_charge_id, provider_charge_id, payload, amount, currency, created_at, decision_id, correlation_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    payment_insert_values(
                        user_id=int(message.from_user.id),
                        telegram_charge_id=charge_id,
                        provider_charge_id=provider_id or None,
                        payload=payload,
                        amount=int(sp.total_amount or 0),
                        currency=(sp.currency or "").strip() or None,
                        created_at=created_at,
                        decision_id=decision_id,
                        correlation_id=correlation_id,
                    ),
                )
                n = conn.execute("SELECT changes() AS n").fetchone()["n"]
                if int(n) != 1:
                    conn.execute("ROLLBACK")
                    return

            if payload.startswith("gift:"):
                code = payload.split(":", 1)[1].strip()
                mark_gift_paid_tx(conn, code, payment_id=charge_id or provider_id or None)
                g = conn.execute(
                    "SELECT days, recipient_id FROM gift_codes WHERE code=?",
                    (code,),
                ).fetchone()
                gifted_days = int(g[0] if g else 0)
                recipient_id = (g[1] if g else None)

                bonus = 0
                if gifted_days >= 20:
                    bonus = 5
                elif gifted_days > 0:
                    bonus = 3

                if bonus > 0:
                    conn.execute(
                        "INSERT OR IGNORE INTO gift_bonus_log(code, user_id, bonus_days, created_at_utc) VALUES(?,?,?,datetime('now'))",
                        (code, int(message.from_user.id), int(bonus)),
                    )
                    applied = conn.execute("SELECT changes() AS n").fetchone()["n"]
                    if int(applied) == 1:
                        grant_tx(conn, int(message.from_user.id), "both", int(bonus))
                        conn.execute(
                            "INSERT INTO bonus_grants(user_id, days, source, related_user_id, granted_at_utc) VALUES(?,?,?,?,datetime('now'))",
                            (int(message.from_user.id), int(bonus), "gift", int(recipient_id) if recipient_id is not None else None),
                        )

                conn.execute("COMMIT")

                log_event(message.from_user.id, "gift_paid", {"code": code, "amount": sp.total_amount})
                await deliver_gift_message(message, code)
                return

            plan_id = 0
            if payload.startswith("sub:"):
                try:
                    plan_id = int(payload.split(":", 1)[1].strip() or 0)
                except (ValueError, TypeError):
                    plan_id = 0
            if not plan_id:
                plan_id = get_plan_id(message.from_user.id) or 0

            plan = get_plan_by_id(int(plan_id)) if plan_id else None
            if plan:
                grant_tx(conn, int(message.from_user.id), str(plan["scope"]), int(plan["days"]))
                conn.execute("COMMIT")
            else:
                conn.execute("COMMIT")
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    if plan:
        try:
            paid_at = utc_now().replace(tzinfo=None, microsecond=0).isoformat()
            with db() as conn:
                conn.execute(
                    "UPDATE subscriptions SET paid_at=? WHERE user_id=?",
                    (paid_at, int(message.from_user.id)),
                )
        except (sqlite3.Error, RuntimeError):
            logger.exception("failed to set paid_at")

        try:
            from datetime import datetime, timedelta
            with db() as conn:
                row = conn.execute(
                    "SELECT expires_at FROM subscriptions WHERE user_id=?",
                    (int(message.from_user.id),),
                ).fetchone()
            if row and row["expires_at"]:
                exp = datetime.fromisoformat(row["expires_at"]).replace(microsecond=0)
                run_at = (exp - timedelta(days=3)).replace(microsecond=0)
                if run_at > utc_now().replace(tzinfo=None, microsecond=0):
                    add_job(int(message.from_user.id), "sub_expiring_soon", run_at.isoformat(), {"expires_at": exp.isoformat()})
        except (sqlite3.Error, RuntimeError):
            logger.exception("sub expiring soon scheduling failed")

        cancel_funnel(message.from_user.id)
        try:
            from services.jobs import cancel_funnel2
            cancel_funnel2(message.from_user.id)
        except ImportError:
            logger.debug("cancel_funnel2 import unavailable", exc_info=True)
        except (RuntimeError, ValueError):
            logger.exception("cancel_funnel2 failed")

        clear_plan(message.from_user.id)

        try:
            from datetime import datetime, timedelta
            with db() as conn:
                row = conn.execute(
                    "SELECT expires_at FROM subscriptions WHERE user_id=?",
                    (int(message.from_user.id),),
                ).fetchone()
            if row and row["expires_at"]:
                exp = datetime.fromisoformat(row["expires_at"]).replace(microsecond=0)
                run_at = (exp + timedelta(days=3)).replace(microsecond=0)
                if run_at > utc_now().replace(tzinfo=None, microsecond=0):
                    add_job(int(message.from_user.id), "funnel2_expired_return_3d", run_at.isoformat(), {"expires_at": exp.isoformat()})
        except (sqlite3.Error, RuntimeError):
            logger.exception("funnel2 schedule failed")

        referrer = get_referrer(message.from_user.id)
        if referrer and not reward_already_given(message.from_user.id) and can_reward_referrer(referrer):
            bought_days = int(plan["days"])
            bonus = int(settings.REF_BONUS_MONTH_DAYS) if bought_days >= 30 else int(settings.REF_BONUS_WEEK_DAYS)
            grant(referrer, "both", bonus)
            mark_reward_given(message.from_user.id, bonus)

            buyer_tag = f"@{message.from_user.username}" if message.from_user.username else f"пользователь {message.from_user.id}"
            period = "1 месяц" if bought_days >= 30 else "1 неделю"
            txt = (
                f"🎁 По Вашей рекомендации {buyer_tag} оплатил подписку на {period}.\n"
                f"В связи с этим Вам бонус: +{bonus} касания ресурсных аудиотрансов в подарок!"
            )
            try:
                await message.bot.send_message(referrer, txt)
            except (TelegramAPIError, asyncio.TimeoutError):
                logger.exception("failed to notify referrer")

        await message.answer(
            "✅ Оплата прошла. Подписка активирована.\n\n"
            "Чтобы всё работало идеально — назначьте удобное время получения утреннего и вечернего транса.",
            reply_markup=kb_after_paid(),
        )

        try:
            from datetime import datetime, timedelta, timezone
            cancel_jobs(int(message.from_user.id), prefix="after_paid_setup_ping")
            run_at = (datetime.now(timezone.utc) + timedelta(hours=4)).replace(microsecond=0).isoformat()
            add_job(int(message.from_user.id), "after_paid_setup_ping", run_at, {})
        except (sqlite3.Error, RuntimeError):
            logger.exception("after_paid_setup_ping schedule failed")
        return

    await message.answer("✅ Оплата прошла.", reply_markup=kb_after_paid())