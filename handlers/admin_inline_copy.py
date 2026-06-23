from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from handlers.admin_inline_common import AdminCtx, safe_edit_admin
from handlers.text_input import MarketingCopyState
from services.roles import ROLE_ADMIN, ROLE_MARKETING

from core.ai.decision_core import DecisionCore
from core.ai.action_gateway import execute as sovereign_execute
from core.ai.decision_types import WorldState


from core.callback_utils import safe_answer_callback


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


def _callback_user_id(cb: CallbackQuery) -> int:
    user = cb.from_user
    return int(user.id)


def _format_ai_price_recommendations(res: dict) -> str:
    if not res.get("ok"):
        reason = str(res.get("reason") or "неизвестная причина")
        return (
            "💡 Подсказка по ценам\n\n"
            f"Сейчас не получилось подготовить совет: {reason}.\n\n"
            "Это только подсказка для вас. Бот сам цены не меняет.\n\n"
            "Чтобы изменить цены: Админка → Тарифы → Изменить тарифы."
        )

    reco = dict(res.get("recommendation") or {})
    snapshot = dict(res.get("snapshot") or {})
    demand = dict(snapshot.get("by_scope") or {})

    labels = {
        "morning": "Утренние практики",
        "evening": "Вечерние практики",
        "both": "Утро и вечер вместе",
    }

    lines = [
        "💡 Подсказка по ценам",
        "",
        "Я посмотрел последние оплаты и подготовил совет. Цены сами не меняются — решение остаётся за вами.",
        "",
        "Оплаты за последние дни:",
    ]
    if demand:
        for scope in ("morning", "evening", "both"):
            lines.append(f"• {labels[scope]}: {int(demand.get(scope, 0) or 0)}")
    else:
        lines.append("• Оплат пока не найдено")

    lines.append("")
    lines.append("Что можно сделать с ценами:")
    for scope in ("morning", "evening", "both"):
        multiplier = float(reco.get(scope, 1.0) or 1.0)
        if multiplier > 1.03:
            advice = f"можно осторожно поднять примерно на {round((multiplier - 1.0) * 100)}%"
        elif multiplier < 0.97:
            advice = f"можно осторожно снизить примерно на {round((1.0 - multiplier) * 100)}%"
        else:
            advice = "лучше пока оставить без изменений"
        lines.append(f"• {labels[scope]}: {advice}")

    comment = str(reco.get("comment") or "").strip()
    if comment:
        lines.extend(["", "Почему так:", comment])

    lines.extend([
        "",
        "Чтобы поменять цены: Админка → Тарифы → Изменить тарифы.",
    ])
    return "\n".join(lines)


async def handle(cb: CallbackQuery, state: FSMContext, data: str, ctx: AdminCtx) -> bool:
    if data == "admin:copy:menu":
        # Доступ: marketing/admin/superadmin
        if not (ROLE_MARKETING in ctx.roles or ROLE_ADMIN in ctx.roles or ctx.is_superadmin):
            await safe_answer_callback(cb, "", show_alert=False)
            return True

        await state.clear()
        await state.set_state(MarketingCopyState.key)
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏠 Админка", callback_data="admin:menu")]]
        )
        message = _callback_message(cb)
        if message is None:
            return True
        await message.answer(
            "✍️ Тексты для сообщений\n\n"
            "Я помогу подготовить два варианта текста для выбранного шага. "
            "Тексты будут сохранены, а бот сможет использовать их автоматически.\n\n"
            "Шаг 1 из 3. Скопируйте и отправьте одно слово из списка:\n\n"
            "• nudge — мягкое напоминание\n"
            "• postdemo — сообщение после пробной практики\n"
            "• offer — предложение подписки\n"
            "• offer_nextday — предложение на следующий день\n"
            "• deadline — напоминание перед окончанием предложения\n"
            "• lastcall — последнее напоминание\n\n"
            "Важно: помощник пишет только тексты для администратора и не даёт людям медицинских обещаний.",
            reply_markup=back_kb,
        )
        return True

    if data == "admin:ai:prices":
        # Доступ: marketing/admin/superadmin
        if not (ROLE_MARKETING in ctx.roles or ROLE_ADMIN in ctx.roles or ctx.is_superadmin):
            await safe_answer_callback(cb, "", show_alert=False)
            return True

        # Sovereign execution: DecisionCore decides the action; handler only executes.
        world: WorldState = {"intent": "admin_ai_prices", "user_id": _callback_user_id(cb)}
        decision = DecisionCore.instance().decide(world)

        class _AdminPricesRunner:
            async def run(self, payload: dict):
                if str(payload.get("type")) != "admin_ai_prices":
                    return None

                from services.ai import recommend_prices, record_price_recommendation

                res = await asyncio.to_thread(recommend_prices)
                try:
                    await asyncio.to_thread(record_price_recommendation, res)
                except (TelegramAPIError, asyncio.TimeoutError):
                    logging.getLogger(__name__).exception("record_price_recommendation failed")

                return _format_ai_price_recommendations(res)

        txt_result = await sovereign_execute(decision, runner=_AdminPricesRunner())
        txt_result = str(txt_result or "💡 Подсказка по ценам\n\nПока нет данных для совета.")
        await safe_edit_admin(
            cb,
            state,
            txt_result,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")]]
            ),
        )
        return True

    return False
