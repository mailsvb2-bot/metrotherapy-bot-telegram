from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.admin_inline_common import AdminCtx, safe_edit_admin
from handlers.text_input import MarketingCopyState
from services.roles import ROLE_ADMIN, ROLE_MARKETING

from core.ai.decision_core import DecisionCore
from core.ai.action_gateway import execute as sovereign_execute
from core.ai.decision_types import WorldState


from core.callback_utils import safe_answer_callback


def _format_ai_price_recommendations(res: dict) -> str:
    if not res.get("ok"):
        reason = res.get("reason") or "unknown"
        return (
            "🤖 AI-рекомендации цен\n\n"
            f"Не удалось получить рекомендации: {reason}.\n\n"
            "AI здесь работает только как советчик для администратора. "
            "Цены автоматически не меняются.\n\n"
            "Чтобы изменить цены — откройте: Админка → Тарифы → Изменить тарифы."
        )

    reco = dict(res.get("recommendation") or {})
    snapshot = dict(res.get("snapshot") or {})
    demand = dict(snapshot.get("by_scope") or {})

    labels = {
        "morning": "Утренний тариф",
        "evening": "Вечерний тариф",
        "both": "Полный доступ",
    }

    lines = [
        "🤖 AI-рекомендации цен",
        "",
        "Роль AI: маркетинговый советчик для администратора. Цены автоматически не применяются.",
        "",
        "Спрос за период:",
    ]
    if demand:
        for scope in ("morning", "evening", "both"):
            lines.append(f"• {labels[scope]}: {int(demand.get(scope, 0) or 0)} оплат")
    else:
        lines.append("• Оплат за период не найдено")

    lines.append("")
    lines.append("Рекомендованные коэффициенты:")
    for scope in ("morning", "evening", "both"):
        multiplier = float(reco.get(scope, 1.0) or 1.0)
        lines.append(f"• {labels[scope]}: ×{multiplier:.2f}")

    comment = str(reco.get("comment") or "").strip()
    if comment:
        lines.extend(["", "Комментарий:", comment])

    lines.extend([
        "",
        "Чтобы изменить цены — откройте: Админка → Тарифы → Изменить тарифы.",
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
            inline_keyboard=[[InlineKeyboardButton(text="🏠 Админ-меню", callback_data="admin:menu")]]
        )
        await cb.message.answer(
            "🤖 AI-копирайтер автоворонки\n\n"
            "Роль AI: маркетинговый помощник администратора. Не терапевт.\n\n"
            "Шаг 1/3: отправьте ключ шага (точно как в списке):\n"
            "• nudge\n"
            "• postdemo\n"
            "• offer\n"
            "• offer_nextday\n"
            "• deadline\n"
            "• lastcall\n\n"
            "ℹ️ Два варианта A/B сохраняются в базе и начнут использоваться автоматически.",
            reply_markup=back_kb,
        )
        return True

    if data == "admin:ai:prices":
        # Доступ: marketing/admin/superadmin
        if not (ROLE_MARKETING in ctx.roles or ROLE_ADMIN in ctx.roles or ctx.is_superadmin):
            await safe_answer_callback(cb, "", show_alert=False)
            return True

        # Sovereign execution: DecisionCore decides the action; handler only executes.
        world: WorldState = {"intent": "admin_ai_prices", "user_id": int(cb.from_user.id)}
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
        txt_result = str(txt_result or "🤖 AI-рекомендации цен\n\nНет данных.")
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
