from __future__ import annotations
import logging
import sqlite3

from services.sla import record as sla_record
from services.bg import tm
from services.fast_send_audio import send_audio_cached

from datetime import timedelta
from core.time_utils import utc_now
from services.jobs import add_job, cancel_post_prompt

import asyncio

from aiogram import Router, F
from aiogram.types import CallbackQuery, BufferedInputFile
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError

from keyboards.inline import kb_mood_scale, kb_mood_done, kb_body_question, kb_after_post_actions, kb_post_show_chart
from keyboards.inline import kb_menu_only
from services.db import mark_delivery_once
from services.idempotency import wall_key
from services.idempotency_keys import for_demo_click, for_session
from services.mood import set_pre, set_post, get_session, mark_audio_sent, last_delta
from services.events import log_event
from services.audio_anchor import get_by_anchor
from services.catalog import AudioCatalog
# Контракт: запись факта отправки демо живёт в demo_analytics.
# В старых ветках файл мог называться demo_events — оставляем только корректный импорт.
from services.demo_analytics import record_demo_sent
from services.body import pick_body_question, save_body_feedback, technique_for_area
from services.audio_cache import get_cached_file_id, save_cached_file_id
from services.support_ai import decide_support_pre
from services.subscription import register_touch


from core.callback_utils import safe_answer_callback
router = Router()


@router.callback_query(F.data.regexp(r"^post:chart:\d+$"))
async def post_show_chart(cb: CallbackQuery):
    try:
        await safe_answer_callback(cb, 'Готовлю график…')
    except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError):
        logging.getLogger(__name__).debug("cb.answer failed", exc_info=True)
    except asyncio.TimeoutError:
        logging.getLogger(__name__).debug("cb.answer failed", exc_info=True)
    parts = (cb.data or "").split(":")
    if len(parts) != 3:
        return
    _, _, sid_raw = parts
    try:
        sid = int(sid_raw)
    except (ValueError, RuntimeError):
        logging.getLogger(__name__).exception("Unhandled exception")
        return

    uid = int(cb.from_user.id)

    # Строим график + отправляем фото в фоне, чтобы апдейт не висел (и не попадал в SLOW).
    from services.bg import tm
    bot = cb.bot
    chat_id = int(cb.message.chat.id)

    async def _build_and_send() -> None:
        try:
            from services.mood import series
            from services.charts import plot_mood
            from aiogram.types import BufferedInputFile
            from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError
            import asyncio

            rows = series(uid, kind=None, limit=120)
            if not rows:
                await bot.send_message(chat_id, "Пока нет данных для графика. Сначала пройдите хотя бы одну сессию.")
                return

            png = await asyncio.to_thread(plot_mood, "Изменение моего состояния", rows)
            # Кнопка "🏠 Меню" должна быть прямо под графиком, чтобы не приходилось искать возврат.
            await bot.send_photo(
                chat_id,
                BufferedInputFile(png, filename="mood.png"),
                caption="График изменения состояния",
                reply_markup=kb_menu_only(),
            )
        except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError):
            logging.getLogger(__name__).exception("post_show_chart failed")
        except asyncio.TimeoutError:
            logging.getLogger(__name__).exception("post_show_chart failed")
        except OSError:
            logging.getLogger(__name__).exception("post_show_chart failed")
        except ValueError:
            logging.getLogger(__name__).exception("post_show_chart failed")
        except RuntimeError:
            logging.getLogger(__name__).exception("post_show_chart failed")
            try:
                await bot.send_message(chat_id, "⚠️ Не удалось построить график. Попробуйте позже.")
            except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError):
                logging.getLogger(__name__).debug("Failed to notify chart error", exc_info=True)
            except asyncio.TimeoutError:
                logging.getLogger(__name__).debug("Failed to notify chart error", exc_info=True)
            return

        # После графика — вопрос про тело (строго в нужной формулировке)
        try:
            from services.body import pick_body_question
            from keyboards.inline import kb_body_question
            q = pick_body_question()
            if q:
                question, kbq = kb_body_question(sid, q.key, "В каком месте тела заметнее напряжение?", q.options)
                await bot.send_message(chat_id, question, reply_markup=kbq)
        except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError):
            logging.getLogger(__name__).exception("post_show_chart body question failed")
        except asyncio.TimeoutError:
            logging.getLogger(__name__).exception("post_show_chart body question failed")
        except ValueError:
            logging.getLogger(__name__).exception("post_show_chart body question failed")
        except RuntimeError:
            logging.getLogger(__name__).exception("post_show_chart body question failed")
        except OSError:
            logging.getLogger(__name__).exception("post_show_chart body question failed")

    tm().create(_build_and_send())

    # После постановки фоновой задачи — выходим сразу.
    return

