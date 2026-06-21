from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

from core.callback_utils import safe_answer_callback
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


def _all_plans_sync() -> list[dict[str, Any]]:
    return [dict(p) for p in get_plans(include_inactive=True)]


def _tariff_price_sync(code: str) -> Any:
    with get_connection() as conn:
        row = conn.execute("SELECT price FROM plans WHERE code=?", (code,)).fetchone()
    if row is None:
        return "не найдено"
    return row["price"] if hasattr(row, "keys") else row[0]


def _tariff_dynamics_sync() -> tuple[list[Any], list[Any]]:
    with get_connection() as conn:
        try:
            price_events = conn.execute(
                "SELECT plan_code, old_price, new_price, changed_at_utc, changed_by FROM plan_price_history ORDER BY changed_at_utc ASC"
            ).fetchall()
        except sqlite3.Error:
            logger.exception("plan_price_history read failed")
            price_events = []

        try:
            payments_daily = conn.execute(
                "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n "
                "FROM payments WHERE status IN ('succeeded','paid','success') GROUP BY substr(created_at, 1, 10) ORDER BY day ASC"
            ).fetchall()
        except sqlite3.Error:
            logger.exception("payments daily read failed")
            payments_daily = []
    return price_events, payments_daily


async def tariffs_edit(cb: CallbackQuery, state: FSMContext, ctx: TariffsCtx) -> None:
    if not ctx.can_manage_tariffs:
        await safe_edit_admin(cb, state, "⛔ Нет доступа к управлению тарифами.", reply_markup=_kb_tariffs_nav())
        return

    plans = await asyncio.to_thread(_all_plans_sync)
    rows = []
    for p in plans:
        code = str(p.get("code"))
        title = str(p.get("title") or code)
        price = int(p.get("price") or 0)
        rows.append([InlineKeyboardButton(text=f"{title} — {price} ₽", callback_data=f"admin:tariffs:pick:{code}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=ADMIN_TARIFFS)])

    pick_rows = rows

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
        await safe_answer_callback(cb)
    except (TelegramAPIError, asyncio.TimeoutError):
        logger.debug("tariffs_edit callback answer failed", exc_info=True)




async def tariffs_pick(cb: CallbackQuery, state: FSMContext, code: str) -> None:
    await state.clear()
    await state.set_state(AdminManageState.waiting_tariff_single_price)
    await state.update_data(tariff_code=code)

    # показать текущую цену
    price = await asyncio.to_thread(_tariff_price_sync, code)

    await safe_edit_admin(
        cb,
        state,
        f"Введите новую цену для тарифа {code}.\n"
        f"Текущая цена: {price} ₽\n\n"
        "Просто отправьте число (например 990)."
    )
    try:
        await safe_answer_callback(cb)
    except (TelegramAPIError, asyncio.TimeoutError):
        logger.debug("tariffs_pick callback answer failed", exc_info=True)




async def tariffs_dynamics(cb: CallbackQuery, state: FSMContext, ctx: TariffsCtx) -> None:
    """График "динамика цен и оплат".

    Показывает:
    - события изменения цены (points)
    - количество оплат в день (line)
    """
    from aiogram.types import BufferedInputFile
    from services.charts import plot_tariffs_dynamics

    price_events, payments_daily = await asyncio.to_thread(_tariff_dynamics_sync)
    if not price_events and not payments_daily:
        return await safe_edit_admin(cb, state, "📈 Динамика\n\nНет данных для построения графика.", reply_markup=_kb_tariffs_nav())

    # Telegram не любит edit_text + photo, отправляем отдельным сообщением
    try:
        await safe_answer_callback(cb, "Строю график…")
    except (TelegramAPIError, asyncio.TimeoutError):
        logger.debug("tariffs_dynamics callback answer failed", exc_info=True)

    png = await asyncio.to_thread(plot_tariffs_dynamics, "Динамика цен и оплат", price_events, payments_daily)
    if cb.message:
        await cb.message.answer_photo(BufferedInputFile(png, filename="tariffs_dynamics.png"), caption="📈 Динамика цен и оплат")
    prices_text = await asyncio.to_thread(_prices_text)
    await safe_edit_admin(cb, state, "📈 Динамика\n\n" + prices_text, reply_markup=_kb_tariffs_nav())



async def handle_tariffs_callback(cb: CallbackQuery, state: FSMContext, data: str, ctx: TariffsCtx) -> bool:
    if data == ADMIN_TARIFFS or data == "admin:tariffs:show":
        if not ctx.can_manage_tariffs:
            await safe_answer_callback(cb, "", show_alert=False)
            return True
        await render_tariffs_menu(cb, state)
        return True

    if data == "admin:tariffs:edit":
        await tariffs_edit(cb, state, ctx)
        return True

    if data.startswith("admin:tariffs:pick:"):
        await tariffs_pick(cb, state, data.split(":", 3)[-1])
        return True

    if data == "admin:tariffs:history":
        await tariffs_history(cb, ctx)
        return True

    if data == "admin:tariffs:dynamics":
        await tariffs_dynamics(cb, state, ctx)
        return True

    return False
