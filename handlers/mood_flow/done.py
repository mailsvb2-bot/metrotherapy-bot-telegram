from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from core.callback_utils import safe_answer_callback
from core.time_utils import utc_now
from services.db import mark_delivery_once
from services.events import log_event
from services.idempotency_keys import for_session
from services.jobs import add_job, cancel_post_prompt
from services.messenger.audio_progress import confirm_pending_audio_delivery
from services.mood import get_session

router = Router()
log = logging.getLogger(__name__)


def _callback_message(cb: CallbackQuery) -> Message | None:
    return cb.message if isinstance(cb.message, Message) else None


@router.callback_query(F.data.regexp(r"^mood:done:\d+$"))
async def mood_done(cb: CallbackQuery) -> None:
    """Confirm only the current user's delivered session and schedule POST score."""

    await safe_answer_callback(cb)
    message = _callback_message(cb)
    parts = str(cb.data or "").split(":")
    if len(parts) != 3:
        return
    try:
        session_id = int(parts[2])
    except ValueError:
        return

    user_id = int(cb.from_user.id)
    session = get_session(session_id)
    if session is None:
        if message is not None:
            await message.answer("ℹ️ Эта кнопка устарела. Откройте текущий маршрут заново.")
        return
    if int(session.user_id) != user_id:
        log_event(
            user_id,
            "foreign_mood_done_rejected",
            {"session_id": session_id, "owner_user_id": int(session.user_id)},
        )
        if message is not None:
            await message.answer("ℹ️ Эта кнопка относится к другой сессии и не может быть использована.")
        return
    if int(session.audio_sent or 0) != 1:
        if message is not None:
            await message.answer("ℹ️ Аудио по этой сессии ещё не было отправлено.")
        return

    sequence_key = "demo" if str(session.source or "") == "demo" else "full_series"
    confirm_pending_audio_delivery(
        user_id,
        platform="telegram",
        sequence_key=sequence_key,
    )

    cancel_post_prompt(user_id, session_id)
    run_at_dt = utc_now().replace(microsecond=0) + timedelta(seconds=1)
    run_at_iso = run_at_dt.isoformat()
    if not mark_delivery_once(
        user_id,
        str(session.kind or ""),
        "post_prompt_schedule",
        for_session(session_id),
    ):
        return

    add_job(
        user_id,
        "post_prompt",
        run_at_iso,
        {"session_id": str(session_id), "run_at": int(run_at_dt.timestamp())},
    )
    log.debug("post prompt scheduled user_id=%s session_id=%s", user_id, session_id)
