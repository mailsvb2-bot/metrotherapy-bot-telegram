from __future__ import annotations

import asyncio
import logging
import sqlite3
import urllib.parse

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, LabeledPrice, InlineKeyboardButton, ReplyKeyboardRemove

from config.settings import settings
from keyboards.inline import kb_main
from services.plans import get_plan_by_id
from services.gifts import create_gift, mark_gift_paid
from services.payments.ui import kb, kb_back, kb_gift_tariffs, pick_user_keyboard
from services.payments.common import yookassa_provider_data_receipt, invoice_link_kb
from services.pending import set_pending, peek_pending, pop_pending
from services.gift_store import set_target, get_target, clear_target
from services.events import log_event
from core.runtime.sovereignty.enforcement import get_current_token
from services.promo_texts import get_gift_template


logger = logging.getLogger(__name__)


async def gift_menu(cb: CallbackQuery) -> None:
    await cb.answer()
    # Сначала выбираем получателя, потом тариф и оплата
    set_pending(int(cb.from_user.id), "gift_target", {"from_name": (cb.from_user.full_name or "").strip()})
    log_event(int(cb.from_user.id), "gift_menu", {})

    await cb.message.edit_text(
        "🎁 Подарить подписку\n\n"
        "Сначала выберите, кому Вы хотите подарить подписку (через Telegram).",
        reply_markup=kb([
            [InlineKeyboardButton(text="👤 Выбрать получателя", callback_data="gift:pick_target")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
        ]),
    )


async def gift_pick_target(cb: CallbackQuery) -> None:
    await cb.answer()
    uid = int(cb.from_user.id)
    kb_r = pick_user_keyboard()
    if kb_r is None:
        await cb.message.answer(
            "⚠️ Ваш клиент Telegram не поддерживает выбор пользователя кнопкой.\n"
            "Вы можете подарить подписку вручную: Выберите тариф и после оплаты отправьте ссылку другу.",
            reply_markup=kb_gift_tariffs(back_cb="menu:main"),
        )
        return
    set_pending(uid, "gift_target", {"from_name": (cb.from_user.full_name or "").strip()})
    await cb.message.answer(
        "Выберите получателя в Telegram.\n"
        "После выбора откроется список тарифов.",
        reply_markup=kb_r,
    )


async def gift_pick_cancel(message: Message) -> None:
    """Отмена выбора получателя подарка."""
    uid = int(message.from_user.id) if message.from_user else 0
    if not uid:
        return
    peek = peek_pending(uid)
    if peek and peek.kind == "gift_target":
        pop_pending(uid)
        clear_target(uid)
        from keyboards.inline import kb_menu_only

        await message.answer(
            "✅ Хорошо. Выбор получателя отменён.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer('Главное меню:', reply_markup=kb_main(user_id=message.from_user.id))


async def gift_users_shared(message: Message, state: FSMContext) -> None:
    uid = int(message.from_user.id)
    # выбор получателя не должен попадать в чужие FSM-сценарии
    try:
        await state.clear()
    except (sqlite3.Error, RuntimeError):
        logger.exception("gift_users_shared: failed to clear FSM state (user_id=%s)", uid)

    peek = peek_pending(uid)
    if not peek or peek.kind != "gift_target":
        return
    p = pop_pending(uid)
    if not p:
        return

    try:
        if getattr(message, "user_shared", None) is not None:
            to_id = int(message.user_shared.user_id)
        else:
            shared = message.users_shared
            picked = (shared.users or [])[:1]
            to_id = int(picked[0].user_id) if picked else 0
    except sqlite3.Error:
        logger.exception("gift_users_shared: failed to parse shared user (user_id=%s)", uid)
        to_id = 0
    except (RuntimeError, AttributeError, TypeError):
        logger.exception("gift_users_shared: failed to parse shared user (user_id=%s)", uid)
        to_id = 0
    except (ValueError, IndexError):
        logger.exception("gift_users_shared: failed to parse shared user (user_id=%s)", uid)
        to_id = 0

    if not to_id:
        await message.answer(
            "❌ Не удалось получить пользователя. Попробуйте ещё раз.",
            reply_markup=kb([
                [InlineKeyboardButton(text="👤 Выбрать получателя", callback_data="gift:pick_target")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="gift:menu")],
            ]),
        )
        return

    from keyboards.inline import kb_menu_only

    await message.answer("✅ Получатель выбран.", reply_markup=ReplyKeyboardRemove())

    set_target(uid, to_id)
    log_event(uid, "gift_target_picked", {"to_id": to_id})

    await message.answer(
        "Теперь выберите тариф для подарка:",
        reply_markup=kb_gift_tariffs(back_cb="gift:menu"),
    )


async def gift_buy(cb: CallbackQuery) -> None:
    await cb.answer()

    try:
        _, _, plan_id_s, expected_s = (cb.data or "").split(":", 3)
        plan_id = int(plan_id_s)
        expected_price = int(expected_s)
    except (ValueError, AttributeError):
        logger.exception("gift_buy: bad callback")
        await cb.message.edit_text("❌ Некорректный тариф.", reply_markup=kb_back("gift:menu"))
        return

    plan = get_plan_by_id(plan_id)
    if not plan or not plan.get("is_active"):
        await cb.message.edit_text("❌ Тариф не найден.", reply_markup=kb_back("gift:menu"))
        return

    current_price = int(plan.get("price") or 0)
    if current_price <= 0:
        await cb.message.edit_text("⚠️ Цена не найдена. Проверьте тарифы.", reply_markup=kb_back("gift:menu"))
        return

    if current_price != expected_price:
        await cb.message.edit_text(
            "⚠️ Цена обновилась. Выберите тариф ещё раз:",
            reply_markup=kb_gift_tariffs(back_cb="gift:menu"),
        )
        return

    scope = str(plan["scope"])
    days = int(plan["days"])
    title = str(plan["title"])

    token = (settings.PAY_PROVIDER_TOKEN or "").strip()
    if not token:
        await cb.message.answer("❌ Не задан PAY_PROVIDER_TOKEN в .env.", reply_markup=kb_back("menu:main"))
        return

    tgt = get_target(int(cb.from_user.id))
    if not tgt:
        await cb.message.answer("Сначала выберите получателя.", reply_markup=kb_back("gift:menu"))
        return

    code = create_gift(int(plan.get('plan_id') or plan.get('id') or 0), cb.from_user.id, recipient_id=tgt.to_id)
    payload = f"gift:{code}"
    tok = get_current_token()
    if tok is not None:
        payload = f"{payload}|d={tok.decision_id}|c={tok.nonce}"

    log_event(cb.from_user.id, "invoice_created", {
        "type": "gift",
        "scope": scope,
        "days": days,
        "price": int(current_price),
        "code": code,
        "to_id": tgt.to_id,
    })
    log_event(cb.from_user.id, "gift_invoice_created", {
        "scope": scope,
        "days": days,
        "price": int(current_price),
        "code": code,
        "to_id": tgt.to_id,
    })

    price_rub = int(current_price)
    if price_rub >= 100000 and price_rub % 100 == 0:
        price_rub = price_rub // 100

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

    provider_data = yookassa_provider_data_receipt(f"Подарок: {title}", price_rub)
    invoice_kwargs = dict(
        title="Подарок: подписка «Метротерапия»",
        description="Подписка считается по рабочим касаниям, а не по календарю.",
        provider_token=token,
        start_parameter="metrotherapy_gift",
        currency="RUB",
        prices=[LabeledPrice(label=f"Подарок: {title}", amount=amount)],
        payload=payload,
        need_email=True,
        send_email_to_provider=True,
        provider_data=provider_data,
    )

    logger.info("gift_buy: invoice requested", extra={"user_id": cb.from_user.id, "plan_id": int(plan_id), "amount": amount})
    try:
        await cb.message.answer_invoice(**invoice_kwargs)
        return
    except TelegramBadRequest as e:
        msg = str(e)
        if "CURRENCY_TOTAL_AMOUNT_INVALID" in msg:
            await cb.message.answer(
                "❌ Ошибка платежа: CURRENCY_TOTAL_AMOUNT_INVALID\n\n"
                f"Подарок: {title}\n"
                f"Цена (руб): {price_rub}\n"
                f"Сумма (коп): {amount}\n\n"
                "Проверьте, что провайдер из @BotFather → Payments поддерживает RUB, "
                "и что сумма не ниже минимальной для провайдера.",
                reply_markup=kb_back("menu:main"),
            )
            return
        logger.exception("gift_buy: answer_invoice failed")
    except TelegramAPIError:
        logger.exception("gift_buy: answer_invoice failed unexpectedly")

    try:
        link = await cb.bot.create_invoice_link(**invoice_kwargs)
    except TelegramBadRequest as e:
        await cb.message.answer(f"❌ Ошибка платежа: {e}", reply_markup=kb_back("menu:main"))
        return
    except TelegramAPIError:
        logger.exception("gift_buy: create_invoice_link failed")
        await cb.message.answer(
            "❌ Не удалось открыть оплату. Проверьте PAY_PROVIDER_TOKEN и настройки провайдера в @BotFather → Payments.",
            reply_markup=kb_back("menu:main"),
        )
        return

    await cb.message.answer(
        "Платёжное окно не открылось автоматически. Откройте оплату по кнопке ниже:",
        reply_markup=invoice_link_kb(link, back_cb="gift:menu"),
    )


async def deliver_gift_message(message: Message, code: str) -> None:
    """Попробовать отправить сообщение получателю; если не получается — дать ссылку дарителю."""
    me = await message.bot.get_me()
    link = f"https://t.me/{me.username}?start=gift_{code}"
    from_name = (message.from_user.full_name or "").strip() or "друг"
    txt = get_gift_template().format(link=link, from_name=from_name)

    sent_ok = 0
    tgt = get_target(message.from_user.id)
    if tgt:
        try:
            await message.bot.send_message(int(tgt.to_id), txt)
            sent_ok = 1
        except (TelegramAPIError, asyncio.TimeoutError):
            logger.info("gift delivery send_message failed", exc_info=True)
            sent_ok = 0

    if sent_ok:
        log_event(message.from_user.id, "gift_delivered_ok", {"code": code, "to_id": int(tgt.to_id) if tgt else None})
        await message.answer(
            "✅ Оплата прошла. Подарок оплачен и отправлен получателю.",
            reply_markup=kb([[InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")]]),
        )
    else:
        log_event(message.from_user.id, "gift_delivered_fail", {"code": code, "to_id": int(tgt.to_id) if tgt else None})
        share_url = "https://t.me/share/url?" + urllib.parse.urlencode({"url": link, "text": txt})
        await message.answer(
            "✅ Оплата прошла. Подарок готов.\n\n"
            "⚠️ Не удалось отправить сообщение выбранному пользователю (обычно это значит, что он ещё не запускал бота).\n"
            "Отправьте ссылку вручную:\n" + link,
            reply_markup=kb([
                [InlineKeyboardButton(text="📨 Поделиться в Telegram", url=share_url)],
                [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")],
            ]),
        )
        await message.answer(
            "Главное меню:",
            reply_markup=kb_main(user_id=message.from_user.id),
        )

    clear_target(message.from_user.id)

