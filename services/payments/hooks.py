from __future__ import annotations

import asyncio
import logging
import sqlite3
import urllib.parse

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, PreCheckoutQuery, InlineKeyboardButton

from core.time_utils import utc_now
from keyboards.inline import kb_main
from services.db import db
from services.plan_store import get_plan_id, clear_plan
from services.plans import get_plan_by_id
from services.subscription import grant, grant_tx
from services.jobs import cancel_funnel, add_job, cancel_jobs
from services.events import log_event
from services.referrals import get_referrer, reward_already_given, mark_reward_given, can_reward_referrer
from services.bonuses import add_grant
from services.gifts import mark_gift_paid_tx
from services.gift_store import get_target, clear_target
from services.promo_texts import get_gift_template
from config.settings import settings

from services.payments.ui import kb_after_paid, kb, kb_back
from services.payments.gift import deliver_gift_message

logger = logging.getLogger(__name__)


async def pre_checkout(pre: PreCheckoutQuery) -> None:
    # Важно отвечать быстро (<=10 сек)
    try:
        logger.info(
            "pre_checkout_query: uid=%s currency=%s total=%s payload=%s",
            getattr(pre.from_user, "id", None),
            getattr(pre, "currency", None),
            getattr(pre, "total_amount", None),
            (getattr(pre, "invoice_payload", "") or "")[:64],
        )
    except (AttributeError, TypeError, ValueError):
        logger.exception("pre_checkout_query log failed")
    try:
        await pre.answer(ok=True)
    except (TelegramAPIError, asyncio.TimeoutError):
        logger.exception("pre_checkout_query answer failed")


async def successful_payment(message: Message) -> None:
    sp = message.successful_payment
    payload = (sp.invoice_payload or "").strip()
    # Decision attribution: payload may include |d=<decision_id>|c=<correlation_id>
    decision_id = None
    correlation_id = None
    if '|d=' in payload:
        try:
            parts = payload.split('|')
            base = parts[0]
            for p in parts[1:]:
                if p.startswith('d='): decision_id = p[2:] or None
                if p.startswith('c='): correlation_id = p[2:] or None
            payload = base
        except (AttributeError, TypeError, ValueError):
            decision_id = None; correlation_id = None
    log_event(message.from_user.id, "invoice_paid", {"payload": payload, "amount": sp.total_amount})

    # Idempotency: protect from duplicate successful_payment updates
    charge_id = (getattr(sp, "telegram_payment_charge_id", "") or "").strip()
    provider_id = (getattr(sp, "provider_payment_charge_id", "") or "").strip()
    # We run the critical part in a single transaction to avoid half-states.
    # Scheduling/notifications can be done after commit.
    with db() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            if charge_id:
                conn.execute(
                    "INSERT OR IGNORE INTO payments(user_id, telegram_charge_id, provider_charge_id, payload, amount, currency, created_at, decision_id, correlation_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        int(message.from_user.id),
                        charge_id,
                        provider_id or None,
                        payload,
                        int(sp.total_amount or 0),
                        (sp.currency or "").strip() or None,
                        decision_id,
                        correlation_id,
                        utc_now().replace(tzinfo=None, microsecond=0).isoformat(),
                    ),
                )
                n = conn.execute("SELECT changes() AS n").fetchone()["n"]
                if int(n) != 1:
                    conn.execute("ROLLBACK")
                    return

            # Gift payment
            if payload.startswith("gift:"):
                code = payload.split(":", 1)[1].strip()
                mark_gift_paid_tx(conn, code, payment_id=charge_id or provider_id or None)

                # Bonus for gifting (idempotent via gift_bonus_log PK)
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
                        # bookkeeping (best-effort): bonus_grants table
                        conn.execute(
                            "INSERT INTO bonus_grants(user_id, days, source, related_user_id, granted_at_utc) VALUES(?,?,?,?,datetime('now'))",
                            (int(message.from_user.id), int(bonus), "gift", int(recipient_id) if recipient_id is not None else None),
                        )

                conn.execute("COMMIT")

                log_event(message.from_user.id, "gift_paid", {"code": code, "amount": sp.total_amount})
                await deliver_gift_message(message, code)
                return

            # Subscription payment
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
        # paid_at for analytics
        try:
            paid_at = utc_now().replace(tzinfo=None, microsecond=0).isoformat()
            with db() as conn:
                conn.execute(
                    "UPDATE subscriptions SET paid_at=? WHERE user_id=?",
                    (paid_at, int(message.from_user.id)),
                )
        except (sqlite3.Error, RuntimeError):
            logger.exception("failed to set paid_at")

        # reminder 3 days before expires
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
            # Funnel 2.0 может быть отключён/не поставлен — это допустимо.
            logger.debug("cancel_funnel2 import unavailable", exc_info=True)
        except (RuntimeError, ValueError):
            logger.exception("cancel_funnel2 failed")

        clear_plan(message.from_user.id)

        # Funnel 2.0: 3 days after expiry
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

        # Referral bonus
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
