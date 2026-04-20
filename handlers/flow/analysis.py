from __future__ import annotations
import logging

import os


from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import CallbackQuery, Message, BufferedInputFile

import asyncio
from datetime import datetime
from core.time_utils import today_tz

from keyboards.inline import (
    kb_settings_menu,
    kb_back_main,
    kb_main,
    kb_ref_bonus_actions,
    kb_state_after_charts,
    kb_settings_locked,
    kb_menu_only,
)
from services.db import db
from services.subscription import has_access
from services.events import log_event
# (ленивый импорт графиков внутри хендлеров)
from services.mood import series
from services.bonuses import compute_bonus_stats, paid_referrals_count, gift_grants_count, gift_days_granted
from services.pending import set_pending, peek_pending, pop_pending
from config.settings import settings


router = Router()


async def safe_edit(message: Message, text: str, reply_markup=None, parse_mode=None, **kwargs):
    """Безопасный edit_text.

    Telegram может вернуть `message is not modified`, если пользователь нажал кнопку повторно.
    По Установке F/UX: не падаем и не спамим.
    """
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


@router.callback_query(F.data == "settings:state")
async def settings_state(cb: CallbackQuery):
    # Не строим графики сразу — сначала даём выбор периода (без лишних пунктов)
    try:
        await cb.answer()
    except (TelegramAPIError, TelegramBadRequest, asyncio.TimeoutError):
        logging.getLogger(__name__).debug("cb.answer failed", exc_info=True)

    from keyboards.inline import kb_state_period_menu

    await safe_edit(
        cb.message,
        "📈 Анализ моего состояния\n\n"
        "Можно поставить быструю оценку прямо сейчас (1–10) — она сохранится, даже если график построите позже.\n\n"
        "Выберите действие:",
        reply_markup=kb_state_period_menu(),
        parse_mode=None,
    )


@router.callback_query(F.data == "state:rate")
async def state_rate_menu(cb: CallbackQuery):
    try:
        await cb.answer()
    except (TelegramAPIError, TelegramBadRequest, asyncio.TimeoutError):
        logging.getLogger(__name__).debug("cb.answer failed", exc_info=True)

    from keyboards.inline import kb_state_rate_scale

    await safe_edit(
        cb.message,
        "⭐ Оцените своё состояние прямо сейчас (1 — хуже, 10 — лучше):",
        reply_markup=kb_state_rate_scale(),
        parse_mode=None,
    )


@router.callback_query(F.data.regexp(r"^state:rate:\d+$"))
async def state_rate_click(cb: CallbackQuery):
    try:
        await cb.answer("Сохранено")
    except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError):
        logging.getLogger(__name__).debug("cb.answer failed", exc_info=True)
    except asyncio.TimeoutError:
        logging.getLogger(__name__).debug("cb.answer failed", exc_info=True)

    parts = (cb.data or "").split(":")
    if len(parts) != 3:
        return
    try:
        rating = int(parts[2])
    except (TypeError, ValueError):
        return

    from services.state_ratings import add_rating
    uid = int(cb.from_user.id)

    ok = add_rating(uid, rating)
    if not ok:
        return await cb.message.answer("⚠️ Не удалось сохранить оценку. Попробуйте ещё раз.")

    # После сохранения возвращаем пользователя в меню анализа
    from keyboards.inline import kb_state_period_menu
    await safe_edit(
        cb.message,
        f"✅ Сохранил: {rating}/10\n\nВыберите период, чтобы построить график:",
        reply_markup=kb_state_period_menu(),
        parse_mode=None,
    )


@router.callback_query(F.data.in_({"state:today", "state:yesterday", "state:all"}))
async def state_period(cb: CallbackQuery):
    # Быстро подтверждаем нажатие (важно: иначе query устаревает при долгой отрисовке)
    try:
        await cb.answer("Готовлю графики…")
    except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError):
        logging.getLogger(__name__).debug("cb.answer failed", exc_info=True)
    except asyncio.TimeoutError:
        logging.getLogger(__name__).debug("cb.answer failed", exc_info=True)

    from services.bg import tm
    bot = cb.bot
    chat_id = int(cb.message.chat.id)
    uid = int(cb.from_user.id)
    period = (cb.data or "").split(":", 1)[1] if ":" in (cb.data or "") else "all"

    async def _build_and_send() -> None:
        try:
            import os
            import asyncio
            from datetime import datetime, timedelta, timezone
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            from aiogram.types import BufferedInputFile
            from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
            from services.mood import series
            from services.state_ratings import series as state_series
            from services.charts import plot_mood, plot_overall, plot_state_ratings
            from services.settings import get_user_tz
            from keyboards.inline import kb_menu_only

            # Определяем дату пользователя (если TZ не задан — берём UTC)
            try:
                tz_name = (get_user_tz(uid) or os.environ.get("TIMEZONE") or "UTC").strip() or "UTC"
                tz = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError):
                tz = timezone.utc

            today = datetime.now(tz).date()
            if period == "yesterday":
                day = (today - timedelta(days=1)).isoformat()
                title_suffix = " (вчера)"
            elif period == "today":
                day = today.isoformat()
                title_suffix = " (сегодня)"
            else:
                day = None
                title_suffix = ""

            # 1) Быстрая шкала 1..10
            rows_state = state_series(uid, day=day, limit=200) if day else state_series(uid, limit=400)

            # 2) Сессии (до/после аудио)
            rows_work = series(uid, kind="work")
            rows_home = series(uid, kind="home")

            if day:
                rows_work = [r for r in rows_work if (r.get("day") == day)]
                rows_home = [r for r in rows_home if (r.get("day") == day)]

            if not rows_state and not rows_work and not rows_home:
                await bot.send_message(
                    chat_id,
                    "Пока нет данных за выбранный период.\n\n"
                    "Сначала поставьте оценку (1–10) в разделе «Анализ» или пройдите хотя бы одну сессию (оценка до/после аудио).",
                )
                return

            sent_any = False
            last_msg = None

            # Быстрая шкала — всегда первой (если есть)
            if rows_state:
                png = await asyncio.to_thread(plot_state_ratings, f"Состояние{title_suffix}", rows_state)
                last_msg = await bot.send_photo(
                    chat_id,
                    BufferedInputFile(png, filename="state.png"),
                    caption=f"Состояние{title_suffix}",
                )
                sent_any = True

            if rows_work:
                png = await asyncio.to_thread(plot_mood, f"Дорога на работу{title_suffix}", rows_work)
                last_msg = await bot.send_photo(
                    chat_id,
                    BufferedInputFile(png, filename="work.png"),
                    caption=f"Дорога на работу{title_suffix}",
                )
                sent_any = True

            if rows_home:
                png = await asyncio.to_thread(plot_mood, f"Дорога домой{title_suffix}", rows_home)
                last_msg = await bot.send_photo(
                    chat_id,
                    BufferedInputFile(png, filename="home.png"),
                    caption=f"Дорога домой{title_suffix}",
                )
                sent_any = True

            if rows_work and rows_home:
                try:
                    png = await asyncio.to_thread(plot_overall, rows_work, rows_home)
                    last_msg = await bot.send_photo(
                        chat_id,
                        BufferedInputFile(png, filename="overall.png"),
                        caption=f"Общая динамика{title_suffix}",
                    )
                    sent_any = True
                except OSError:
                    logging.getLogger(__name__).exception("plot_overall failed")
                except ValueError:
                    logging.getLogger(__name__).exception("plot_overall failed")
                except RuntimeError:
                    logging.getLogger(__name__).exception("plot_overall failed")

            if sent_any and last_msg is not None:
                try:
                    # Кнопка "Меню" должна быть прямо под последним графиком.
                    await bot.edit_message_reply_markup(chat_id=chat_id, message_id=int(last_msg.message_id), reply_markup=kb_menu_only())
                except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError):
                    logging.getLogger(__name__).exception("Failed to attach menu button under chart")
                except asyncio.TimeoutError:
                    logging.getLogger(__name__).exception("Failed to attach menu button under chart")
                    await bot.send_message(chat_id, "⬇️", reply_markup=kb_menu_only())
        except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError):
            logging.getLogger(__name__).exception("state_period charts failed")
        except asyncio.TimeoutError:
            logging.getLogger(__name__).exception("state_period charts failed")
        except OSError:
            logging.getLogger(__name__).exception("state_period charts failed")
        except ValueError:
            logging.getLogger(__name__).exception("state_period charts failed")
        except RuntimeError:
            logging.getLogger(__name__).exception("state_period charts failed")
            try:
                await bot.send_message(chat_id, "⚠️ Не удалось построить графики. Попробуйте позже.")
            except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError):
                logging.getLogger(__name__).debug("Failed to notify charts error", exc_info=True)
            except asyncio.TimeoutError:
                logging.getLogger(__name__).debug("Failed to notify charts error", exc_info=True)

    tm().create(_build_and_send())
    return

