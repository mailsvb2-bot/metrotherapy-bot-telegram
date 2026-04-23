from __future__ import annotations

import asyncio
import logging
import sqlite3

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, LabeledPrice

from config.settings import settings
from services.plans import get_plan_by_id, get_plan_by_scope_days
from services.plan_store import set_plan, get_plan, get_plan_id, clear_plan
from services.jobs import cancel_funnel, add_job, cancel_jobs
from services.events import log_event
from core.runtime.sovereignty.enforcement import get_current_token
from services.personalization import get_preface

from services.payments.ui import kb_tariffs, kb_back, kb_pay_selected
from services.payments.common import yookassa_provider_data_receipt, invoice_link_kb


logger = logging.getLogger(__name__)


async def cmd_subscribe(message: Message) -> None:
    await message.answer("💳 Тарифы:", reply_markup=kb_tariffs(message.from_user.id if message.from_user else None))


async def sub_menu(cb: CallbackQuery) -> None:
    preface = await asyncio.to_thread(get_preface, int(cb.from_user.id), 'sub')
    text = f"{preface}💳 Выберите тариф:"
    await cb.message.edit_text(text, reply_markup=kb_tariffs(cb.from_user.id))
    log_event(cb.from_user.id, "view_tariffs", {})
    log_event(cb.from_user.id, "sub_menu_open", {})


async def sub_pick(cb: CallbackQuery) -> None:
    """Выбор тарифа для покупки.

    В callback передаём ожидаемую цену: sub:buy:<plan_id>:<expected_price>
    """
    try:
        _, _, plan_id_s, expected_s = (cb.data or "").split(":")
        plan_id = int(plan_id_s)
        expected_price = int(expected_s)
    except (ValueError, AttributeError):
        logger.exception("sub_pick: bad callback data")
        await cb.message.edit_text("❌ Некорректная кнопка тарифа.", reply_markup=kb_back("sub:menu"))
        return

    plan = await asyncio.to_thread(get_plan_by_id, plan_id)
    if not plan or not plan.get("is_active"):
        await cb.message.edit_text("❌ Тариф не найден.", reply_markup=kb_back("sub:menu"))
        return

    current_price = int(plan.get("price") or 0)
    if current_price <= 0:
        await cb.message.edit_text(
            "⚠️ Цена для выбранного тарифа не задана.\nПроверьте тарифы в админке.",
            reply_markup=kb_back("sub:menu"),
        )
        return

    if current_price != expected_price:
        await cb.message.edit_text(
            "Цена только что обновилась.\nПожалуйста, выберите тариф ещё раз:",
            reply_markup=kb_tariffs(cb.from_user.id),
        )
        return

    scope = str(plan["scope"])
    days = int(plan["days"])  # в текущем UX это количество касаний
    title = str(plan["title"])

    plan_code = str(plan.get("plan_code") or plan.get("code") or "").strip()
    if not plan_code:
        await cb.message.edit_text(
            "❌ У выбранного тарифа нет кода (plan_code/code).\n"
            "Откройте админку → Тарифы и проверьте таблицу plans.",
            reply_markup=kb_back("sub:menu"),
        )
        return

    await asyncio.to_thread(set_plan, cb.from_user.id, scope, days, title, current_price, plan_code)

    await cb.message.edit_text(
        f"✅ Вы выбрали:\n{title}\n\n💰 Стоимость: {current_price} ₽",
        reply_markup=kb_pay_selected(),
    )


async def pay_selected(cb: CallbackQuery) -> None:

    token = (settings.PAY_PROVIDER_TOKEN or "").strip()
    if not token:
        await cb.message.answer(
            "❌ Не задан PAY_PROVIDER_TOKEN в .env.\n"
            "Подключите Payments в @BotFather → Payments и вставьте токен.",
            reply_markup=kb_back("menu:main"),
        )
        return

    plan = await asyncio.to_thread(get_plan, cb.from_user.id)
    if not plan:
        await cb.message.answer("Сначала выберите тариф.", reply_markup=kb_back("sub:menu"))
        return

    # Единственный источник истины — plans (в БД). selected_plan хранит только выбор (plan_id).
    plan_id = await asyncio.to_thread(get_plan_id, cb.from_user.id)
    if not plan_id:
        # fallback for older DB rows
        try:
            current = await asyncio.to_thread(get_plan_by_scope_days, str(plan.get("scope") or ""), int(plan.get("days") or 0))
            plan_id = int(current["plan_id"] if "plan_id" in (current or {}) else current["id"]) if current else 0
        except (TypeError, ValueError, KeyError):
            plan_id = 0
    if not plan_id:
        await cb.message.answer("Сначала выберите тариф.", reply_markup=kb_back("sub:menu"))
        return

    # Берём актуальные параметры из plans (админка может менять бесконечно).
    try:
        current = await asyncio.to_thread(get_plan_by_id, int(plan_id))
        if not current or int(current.get("price") or 0) <= 0:
            await cb.message.answer("Тариф недоступен. Выберите другой.", reply_markup=kb_back("sub:menu"))
            return
        current_price = int(current.get("price") or 0)
        # Если выбранный план был сохранён со старой ценой — показываем кнопку оплатить уже по актуальной.
        plan = {**plan, **current, "plan_id": int(plan_id)}
    except sqlite3.Error:
        logger.exception("pay_selected: failed to load plan from plans by plan_id")
        await cb.message.answer("Ошибка загрузки тарифа. Попробуйте ещё раз.", reply_markup=kb_back("sub:menu"))
        return
    except (ValueError, TypeError, KeyError):
        logger.exception("pay_selected: failed to load plan from plans by plan_id")
        await cb.message.answer("Ошибка загрузки тарифа. Попробуйте ещё раз.", reply_markup=kb_back("sub:menu"))
        return

    price_rub = int(plan["price"])
    if price_rub >= 50000 and price_rub % 100 == 0:
        price_rub = price_rub // 100

    payload = f"sub:{int(plan.get('plan_id') or plan.get('id') or 0)}"
    tok = get_current_token()
    if tok is not None:
        payload = f"{payload}|d={tok.decision_id}|c={tok.nonce}"

    log_event(cb.from_user.id, "invoice_created", {
        "type": "sub",
        "scope": plan["scope"],
        "days": plan["days"],
        "price": price_rub,
    })

    amount = price_rub * 100
    if amount <= 0 or amount > 2_147_483_647:
        await cb.message.answer(
            "❌ Невалидная сумма для платежа.\n\n"
            f"Цена (руб): {price_rub}\n"
            f"Сумма (коп): {amount}\n\n"
            "Проверьте цену тарифа в админке.",
            reply_markup=kb_back("menu:main"),
        )
        return

    provider_data = yookassa_provider_data_receipt(plan.get("title", "Подписка"), price_rub)
    invoice_kwargs = dict(
        title="Подписка «Метротерапия»",
        description="Подписка — это фиксированное количество рабочих касаний в дороге, через которые состояние постепенно выравнивается.",
        provider_token=token,
        start_parameter="metrotherapy_sub",
        currency="RUB",
        prices=[LabeledPrice(label=plan["title"], amount=amount)],
        payload=payload,
        need_email=True,
        send_email_to_provider=True,
        provider_data=provider_data,
    )

    logger.info("pay_selected: invoice requested", extra={"user_id": cb.from_user.id, "plan_id": int(plan_id), "amount": amount})
    try:
        await cb.message.answer_invoice(**invoice_kwargs)
        return
    except TelegramBadRequest as e:
        msg = str(e)
        if "CURRENCY_TOTAL_AMOUNT_INVALID" in msg:
            await cb.message.answer(
                "❌ Ошибка платежа: CURRENCY_TOTAL_AMOUNT_INVALID\n\n"
                f"Тариф: {plan.get('title','-')}\n"
                f"Цена (руб): {price_rub}\n"
                f"Сумма (коп): {price_rub*100}\n\n"
                "Проверьте, что провайдер из @BotFather → Payments поддерживает RUB, "
                "и что сумма не ниже минимальной для провайдера.",
                reply_markup=kb_back("menu:main"),
            )
            return
        if "PAYMENT_PROVIDER_INVALID" in msg:
            await cb.message.answer(
                "❌ PAYMENT_PROVIDER_INVALID\n\n"
                "Проверьте: @BotFather → Payments и PAY_PROVIDER_TOKEN в .env",
                reply_markup=kb_back("menu:main"),
            )
            return
        logger.exception("pay_selected: answer_invoice failed")
    except TelegramAPIError:
        logger.exception("pay_selected: answer_invoice failed unexpectedly")

    try:
        link = await cb.bot.create_invoice_link(**invoice_kwargs)
    except TelegramBadRequest as e:
        await cb.message.answer(f"❌ Ошибка платежа: {e}", reply_markup=kb_back("menu:main"))
        return
    except TelegramAPIError:
        logger.exception("pay_selected: create_invoice_link failed")
        await cb.message.answer(
            "❌ Не удалось открыть оплату. Проверьте PAY_PROVIDER_TOKEN и настройки провайдера в @BotFather → Payments.",
            reply_markup=kb_back("menu:main"),
        )
        return

    await cb.message.answer(
        "Платёжное окно не открылось автоматически. Откройте оплату по кнопке ниже:",
        reply_markup=invoice_link_kb(link, back_cb="sub:menu"),
    )
