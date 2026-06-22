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

from keyboards.inline import kb_mood_scale, kb_mood_done, kb_body_question, kb_after_post_actions, kb_post_show_chart
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


@router.callback_query(F.data.regexp(r"^mood:done:\d+$"))
async def mood_done(cb: CallbackQuery):
    """Пользователь нажал 'Прослушал'. Пост-оценка всегда через +1 секунду."""
    await safe_answer_callback(cb)
    data = (cb.data or "").split(":")
    if len(data) != 3:
        return
    try:
        sid = int(data[2])
    except (ValueError, RuntimeError):
        logging.getLogger(__name__).exception("Unhandled exception")
        return
    s = get_session(sid)
    if not s:
        return

    # Cancel any previously scheduled post-prompt for this session and schedule a new one (через 1 секунду).
    cancel_post_prompt(int(cb.from_user.id), sid)
    run_at_dt = utc_now().replace(microsecond=0) + timedelta(seconds=1)
    run_at_epoch = int(run_at_dt.timestamp())
    run_at_iso = run_at_dt.isoformat()
    # Idempotency: ставим маркер ДО add_job.
    # Это защищает от дублей при повторном callback/рестарте.
    if not mark_delivery_once(int(cb.from_user.id), str(s.kind or ""), "post_prompt_schedule", for_session(sid)):
        return
    add_job(
        int(cb.from_user.id),
        "post_prompt",
        run_at_iso,
        {"session_id": str(sid), "run_at": int(run_at_epoch)},
    )

