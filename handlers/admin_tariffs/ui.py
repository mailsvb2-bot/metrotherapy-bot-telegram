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
from services.practice_token_contract import public_practice_packages, telegram_stars_price, telegram_stars_pricing_mode


from core.callback_utils import safe_answer_callback


def _kb_tariffs_nav() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=ADMIN_TARIFFS)],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="admin:menu")],
        ]
    )


def kb_tariffs_nav() -> InlineKeyboardMarkup:
    """Публичная обёртка для навигационной клавиатуры тарифов."""
    return _kb_tariffs_nav()


def _tariffs_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✏️ Архивные подписки", callback_data="admin:tariffs:edit")],
        [InlineKeyboardButton(text="🗂 Архив", callback_data="admin:tariffs:history")],
        [InlineKeyboardButton(text="📈 Динамика", callback_data="admin:tariffs:dynamics")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="admin:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _prices_text() -> str:
    packages = public_practice_packages()
    package_lines = [
        f"• {package.title} — {package.price_rub} ₽ / {telegram_stars_price(package.package_id)} XTR ({package.package_id})"
        for package in packages
    ]
    plans = get_active_plans()
    legacy_lines = []
    for p in plans:
        legacy_lines.append(f"• {p['title']} — {p['price']} ₽ ({p['code']})")
    legacy = "\n".join(legacy_lines) if legacy_lines else "• нет активных архивных подписок"
    return (
        "Публичные пакеты практик (канонический каталог):\n"
        + "\n".join(package_lines)
        + f"\n\nРежим Stars: {telegram_stars_pricing_mode()}\n"
        + "\nАрхивные тарифы подписки (не управляют публичными пакетами):\n"
        + legacy
    )


def _tariff_history_rows():
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
        log.debug("tariffs menu callback answer failed", exc_info=True)
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
        log.debug("tariff history callback answer failed", exc_info=True)
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
                log.debug("tariff history timestamp parse failed", exc_info=True)
            out.append(f"• {ts_s} | {plan_code}: {old_p} → {new_p} ₽ (by {by})")
        text = "\n".join(out)

    await safe_edit(cb, text, reply_markup=_kb_tariffs_nav())
