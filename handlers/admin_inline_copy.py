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

                res = recommend_prices()
                try:
                    record_price_recommendation(res)
                except (TelegramAPIError, asyncio.TimeoutError):
                    logging.getLogger(__name__).exception("record_price_recommendation failed")

                if not res.get("ok"):
                    reason = res.get("reason") or "unknown"
                    return ("🤖 AI рекомендации цен\n\n"
                        f"Не удалось получить рекомендации: {reason}.\n\n"
                        "Чтобы изменить цены — откройте: Админка → Тарифы → Изменить тарифы.")

                lines = ["🤖 AI рекомендации цен\n"]
                for item in (res.get("items") or []):
                    try:
                        title = str(item.get("title"))
                        price = int(item.get("price"))
                        why = str(item.get("why") or "")
                        lines.append(f"• {title}: {price} ₽\\n  {why}")
                    except (TypeError, ValueError, AttributeError):
                        continue
                    except KeyError:
                        continue
                lines.append("\\nЧтобы изменить цены — откройте: Админка → Тарифы → Изменить тарифы.")
                return "\\n".join(lines)

        txt_result = await sovereign_execute(decision, runner=_AdminPricesRunner())
        txt_result = str(txt_result or "🤖 AI рекомендации цен\\n\\nНет данных.")
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
