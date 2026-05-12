from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError
from aiogram.types import BufferedInputFile, CallbackQuery

from core.callback_utils import safe_answer_callback
from keyboards.inline import kb_menu_only
from services.messenger.progress_charts import build_vk_mood_progress_chart_path
from services.mood import get_session

router = Router()
log = logging.getLogger(__name__)


@router.callback_query(F.data.regexp(r"^post:chart:\d+$"))
async def post_score_chart(cb: CallbackQuery) -> None:
    """Build and send the state-change chart shown after post-score.

    Telegram post-score UI has a direct button:
    `📈 Посмотреть график изменения моего состояния` -> `post:chart:<session_id>`.
    This handler keeps that button alive and uses the same canonical mood-session
    chart builder that VK/MAX use for their `progress_chart` reply.
    """
    try:
        await safe_answer_callback(cb, "Готовлю график…")
    except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError, asyncio.TimeoutError):
        log.debug("post chart callback answer failed", exc_info=True)

    raw = cb.data or ""
    try:
        session_id = int(raw.rsplit(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        return

    session = await asyncio.to_thread(get_session, session_id)
    if session is None:
        await cb.message.answer("⚠️ Не нашёл сессию для построения графика.", reply_markup=kb_menu_only())
        return

    user_id = int(cb.from_user.id)
    chart_path = await asyncio.to_thread(build_vk_mood_progress_chart_path, user_id)
    if chart_path is None:
        await cb.message.answer(
            "📈 Пока недостаточно данных для графика. Пройдите цикл: шкала ДО → аудио → Прослушал → шкала ПОСЛЕ.",
            reply_markup=kb_menu_only(),
        )
        return

    try:
        data = await asyncio.to_thread(chart_path.read_bytes)
        await cb.message.answer_photo(
            BufferedInputFile(data, filename="metrotherapy_state_change.png"),
            caption="📈 График изменения состояния после практики",
            reply_markup=kb_menu_only(),
        )
        log.info("Telegram post-score chart sent: user_id=%s session_id=%s path=%s", user_id, session_id, chart_path)
    except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError, OSError, RuntimeError, ValueError):
        log.exception("Telegram post-score chart send failed")
        await cb.message.answer("⚠️ Не удалось построить или отправить график. Попробуйте позже.", reply_markup=kb_menu_only())
