from __future__ import annotations

import asyncio
import logging
import sqlite3

from aiogram.types import Message, CallbackQuery

from services.plans import get_plan_by_id, get_plan_by_scope_days
from services.plan_store import set_plan, get_plan, get_plan_id, clear_plan
from services.jobs import cancel_funnel, add_job, cancel_jobs
from services.events import log_event
from services.personalization import get_preface

from services.payments.ui import kb_tariffs, kb_back, kb_pay_selected


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

    await asyncio.to_thread(set_plan, cb.from_user.id, scope, days, title, current_price, plan_code, plan_id=plan_id)

    await cb.message.edit_text(
        f"✅ Вы выбрали:\n{title}\n\n💰 Стоимость: {current_price} ₽",
        reply_markup=kb_pay_selected(),
    )


async def pay_selected(cb: CallbackQuery) -> None:
    log_event(int(cb.from_user.id), "legacy_invoice_payment_blocked", {"surface": "pay_selected"})
    await cb.message.answer(
        "Этот старый способ оплаты отключён. Откройте тарифы заново и выберите актуальный пакет:",
        reply_markup=kb_tariffs(int(cb.from_user.id)),
    )
