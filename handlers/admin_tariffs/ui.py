from __future__ import annotations
import asyncio
import sqlite3

from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from handlers.admin_tariffs.common import TariffsCtx, safe_edit, log

from aiogram.types import InlineKeyboardButton
from core.callbacks import ADMIN_TARIFFS
from services.db import get_connection
from services.plans import get_active_plans


from core.callback_utils import safe_answer_callback


def _kb_tariffs_nav() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=ADMIN_TARIFFS)],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="admin:menu")],
        ]
    )


def kb_tariffs_nav() -> InlineKeyboardMarkup:
    """Публичная обёртка для навигационной клавиатуры тарифов.

    Нужна для диагностики (/kb_debug) и чтобы внешние модули не импортировали
    приватную функцию _kb_tariffs_nav().
    """
    return _kb_tariffs_nav()


def _tariffs_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Показать текущие цены", callback_data="admin:tariffs:show")],
            [InlineKeyboardButton(text="✏️ Изменить тарифы", callback_data="admin:tariffs:edit")],
            [InlineKeyboardButton(text="🗂 Архив тарифов", callback_data="admin:tariffs:history")],
            [InlineKeyboardButton(text="📈 Динамика цены и оплат", callback_data="admin:tariffs:dynamics")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")],
        ]
    )


def _prices_text() -> str:
    plans = get_active_plans()
    if not plans:
        return "Тарифы не найдены."
    return "\n".join([f"• {p['title']}: {p['price']} ₽ (код: {p['code']})" for p in plans])


def _tariff_history_rows():
    # Источник истины: plan_price_history (создаётся при init_db).
    with get_connection() as conn:
        try:
            return conn.execute(
                "SELECT plan_code, old_price, new_price, changed_at_utc, changed_by FROM plan_price_history ORDER BY changed_at_utc DESC LIMIT 100"
            ).fetchall()
        except sqlite3.Error:
            log.exception("plan_price_history read failed")
            return []


async def render_tariffs_menu(cb: CallbackQuery, state: FSMContext | None = None) -> None:
    # Rule: every entry callback handler must acknowledge the callback first.
    # This prevents “hanging buttons” if some nested helper forgets to answer.
    try:
        await safe_answer_callback(cb)
    except (TelegramAPIError, asyncio.TimeoutError):
        pass
    text = "💳 Тарифы\n\n" + await asyncio.to_thread(_prices_text)
    if state is None:
        await safe_edit(cb, text, reply_markup=_tariffs_menu_kb())
    else:
        from handlers.admin_inline_common import safe_edit_admin
        await safe_edit_admin(cb, state, text, reply_markup=_tariffs_menu_kb())




async def tariffs_history(cb: CallbackQuery, ctx: TariffsCtx) -> None:
    # Entry handler: always answer callback first to avoid UI spinner.
    try:
        await safe_answer_callback(cb)
    except (TelegramAPIError, asyncio.TimeoutError):
        pass
    rows = await asyncio.to_thread(_tariff_history_rows)

    if not rows:
        text = "🗂 Архив тарифов\n\nПока нет записей об изменениях."
    else:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        from config.settings import settings

        try:
            tz = ZoneInfo(getattr(settings, "TIMEZONE", "Europe/Moscow"))
        except (ZoneInfoNotFoundError, ValueError):
            tz = timezone.utc

        out = ["🗂 Архив тарифов (последние 100)\n"]
        for r in rows:
            try:
                plan_code = r["plan_code"] if hasattr(r, "keys") else r[0]
                old_p = r["old_price"] if hasattr(r, "keys") else r[1]
                new_p = r["new_price"] if hasattr(r, "keys") else r[2]
                ts = r["changed_at_utc"] if hasattr(r, "keys") else r[3]
                by = r["changed_by"] if hasattr(r, "keys") else r[4]
            except (KeyError, IndexError, TypeError):
                # Строка истории может быть частично повреждена/старого формата — пропускаем.
                continue
            except ValueError:
                # Строка истории может быть частично повреждена/старого формата — пропускаем.
                continue
            # prettier timestamp (local)
            ts_s = str(ts)
            try:
                dt = datetime.fromisoformat(ts_s.replace("Z", "+00:00")).astimezone(tz)
                ts_s = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
            out.append(f"• {ts_s} | {plan_code}: {old_p} → {new_p} ₽ (by {by})")
        text = "\n".join(out)

    await safe_edit(cb, text, reply_markup=_kb_tariffs_nav())
