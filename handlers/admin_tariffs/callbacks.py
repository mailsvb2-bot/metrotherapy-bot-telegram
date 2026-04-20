from __future__ import annotations

import asyncio
import logging
import sqlite3

try:
    # Canonical source
    from core.callbacks import ADMIN_TARIFFS as ADMIN_TARIFFS  # type: ignore
except ImportError:
    # Fallback for safety
    ADMIN_TARIFFS = "admin:tariffs"


from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.admin_inline_states import AdminManageState
from handlers.admin_inline_common import safe_edit_admin
from handlers.admin_tariffs.common import TariffsCtx
from handlers.admin_tariffs.ui import _kb_tariffs_nav, _prices_text, render_tariffs_menu, tariffs_history
from services.db import get_connection
from services.plans import get_plans

logger = logging.getLogger(__name__)

async def tariffs_edit(cb: CallbackQuery, state: FSMContext) -> None:
    """Экран редактирования тарифов.

    Важно: используем edit (safe_edit), чтобы админское меню не "убегало".
    """
    await state.clear()
    plans = get_plans(include_inactive=True)

    pick_rows = []
    for p in plans:
        code = str(p.get("code") or "").strip()
        title = str(p.get("title") or "").strip()
        if not code or not title:
            continue
        price = int(p.get("price") or 0)
        pick_rows.append(
            [InlineKeyboardButton(text=f"{title} ({price} ₽)", callback_data=f"admin:tariffs:pick:{code}")]
        )
    pick_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=ADMIN_TARIFFS)])

    await safe_edit_admin(
        cb,
        state,
        "✏️ Изменение тарифов\n\n"
        "1) Выберите тариф кнопкой ниже → я попрошу новую цену.\n\n"
        "ИЛИ\n\n"
        "2) Отправьте сообщением новые цены, по одной строке:\n"
        "<название тарифа или code>=<цена в рублях>\n\n"
        "Пример:\n"
        "Утро — 1 неделя=990\n\n"
        "Важно: вводите цену именно в рублях (например, 1 рубль = 1).\n",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=pick_rows),
    )
    await state.set_state(AdminManageState.waiting_tariffs_text)
    try:
        await cb.answer()
    except (TelegramAPIError, asyncio.TimeoutError):
        pass




async def tariffs_pick(cb: CallbackQuery, state: FSMContext, code: str) -> None:
    await state.clear()
    await state.set_state(AdminManageState.waiting_tariff_single_price)
    await state.update_data(tariff_code=code)

    # показать текущую цену
    price = None
    for p in get_plans(include_inactive=True):
        if str(p.get("code")) == code:
            price = p.get("price")
            break

    await safe_edit_admin(
        cb,
        state,
        f"Введите новую цену для тарифа {code}.\n"
        f"Текущая цена: {price} ₽\n\n"
        "Просто отправьте число (например 990)."
    )
    try:
        await cb.answer()
    except (TelegramAPIError, asyncio.TimeoutError):
        pass




async def tariffs_dynamics(cb: CallbackQuery, state: FSMContext, ctx: TariffsCtx) -> None:
    """График "динамика цен и оплат".

    Показывает:
    - события изменения цены (points)
    - количество оплат в день (line)
    """
    from aiogram.types import BufferedInputFile
    from services.charts import plot_tariffs_dynamics

    with get_connection() as conn:
        # события изменения цен
        try:
            pe = conn.execute(
                "SELECT plan_code, new_price, changed_at_utc FROM plan_price_history ORDER BY changed_at_utc ASC LIMIT 500"
            ).fetchall()
        except sqlite3.Error:
            logger.exception("plan_price_history read failed")
            pe = []

        price_events = []
        for r in pe or []:
            try:
                price_events.append(
                    {
                        "code": r[0] if not hasattr(r, "keys") else r["plan_code"],
                        "new_price": int(r[1] if not hasattr(r, "keys") else r["new_price"]),
                        "created": str(r[2] if not hasattr(r, "keys") else r["changed_at_utc"]),
                    }
                )
            except (TypeError, ValueError, KeyError):
                continue
            except IndexError:
                continue

        # оплаты по дням
        try:
            pr = conn.execute(
                "SELECT substr(created_at,1,10) as day, COUNT(*) as cnt, COALESCE(SUM(amount),0) as amount FROM payments GROUP BY day ORDER BY day ASC LIMIT 365"
            ).fetchall()
        except sqlite3.Error:
            logger.exception("payments read failed")
            pr = []
        payments_daily = []
        for r in pr or []:
            try:
                day = r[0] if not hasattr(r, "keys") else r["day"]
                cnt = r[1] if not hasattr(r, "keys") else r["cnt"]
                amount = r[2] if not hasattr(r, "keys") else r["amount"]
                payments_daily.append({"day": str(day), "cnt": int(cnt), "amount": int(amount)})
            except (TypeError, ValueError, KeyError):
                continue
            except IndexError:
                continue

    if not price_events and not payments_daily:
        return await safe_edit_admin(cb, state, "📈 Динамика\n\nНет данных для построения графика.", reply_markup=_kb_tariffs_nav())

    # Telegram не любит edit_text + photo, отправляем отдельным сообщением
    try:
        await cb.answer("Строю график…")
    except (TelegramAPIError, asyncio.TimeoutError):
        pass

    png = await asyncio.to_thread(plot_tariffs_dynamics, "Динамика цен и оплат", price_events, payments_daily)
    if cb.message:
        await cb.message.answer_photo(BufferedInputFile(png, filename="tariffs_dynamics.png"), caption="📈 Динамика цен и оплат")
    await safe_edit_admin(cb, state, "📈 Динамика\n\n" + _prices_text(), reply_markup=_kb_tariffs_nav())




async def handle_tariffs_callback(cb: CallbackQuery, state: FSMContext, data: str, ctx: TariffsCtx) -> bool:
    if data == ADMIN_TARIFFS or data == "admin:tariffs:show":
        if not ctx.can_manage_tariffs:
            await cb.answer("", show_alert=False)
            return True
        await render_tariffs_menu(cb, state)
        return True

    if not ctx.can_manage_tariffs:
        return False

    if data == "admin:tariffs:edit":
        await tariffs_edit(cb, state)
        return True

    if data.startswith("admin:tariffs:pick:"):
        code = data.split(":", 3)[-1].strip()
        if code:
            await tariffs_pick(cb, state, code)
        else:
            await cb.answer("Некорректный тариф.", show_alert=True)
        return True

    if data == "admin:tariffs:history":
        await tariffs_history(cb, ctx)
        return True

    if data == "admin:tariffs:dynamics":
        await tariffs_dynamics(cb, state, ctx)
        return True

    return False


