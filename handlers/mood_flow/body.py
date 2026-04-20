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


router = Router()


@router.callback_query(F.data.regexp(r"^body:\d+:[^:]+:\d+$"))
async def body_answer(cb: CallbackQuery):
    """Ответ на вопрос "где в теле".

    callback_data:
      body:<session_id>:<q_key>:<idx>
    """
    await cb.answer()
    parts = (cb.data or "").split(":")
    if len(parts) != 4:
        return
    _, sid_raw, q_key, idx_raw = parts
    try:
        sid = int(sid_raw)
        idx = int(idx_raw)
    except (ValueError, RuntimeError):
        logging.getLogger(__name__).exception("Unhandled exception")
        return

    s = get_session(sid)
    if not s:
        return

    q = pick_body_question(force_key=q_key)
    if not q or idx < 0 or idx >= len(q.options):
        return

    area = str(q.options[idx])
    # Контракт: save_body_feedback(user_id, session_id, kind, area)
    save_body_feedback(int(cb.from_user.id), sid, kind=s.kind or "", area=area)
    log_event(int(cb.from_user.id), "body_area", {"area": area, "kind": s.kind, "source": s.source})

    # AI-техника (быстро, сейчас)
    try:
        txt = technique_for_area(area)
    except (ValueError, RuntimeError):
        txt = None

    if not txt:
        txt = "Сделайте 3 медленных выдоха чуть длиннее вдоха — и отметьте, где стало хотя бы на 1% легче."

    await cb.message.answer(txt, reply_markup=kb_post_show_chart(sid))


async def _schedule_post(session_id: str, user_id: int, delay_sec: int, *, kind: str = ""):
    """Единый helper планирования post-подсказки.

    Важно: idempotency ДО add_job.
    """
    run_at_dt = utc_now().replace(microsecond=0) + timedelta(seconds=int(delay_sec))
    run_at_epoch = int(run_at_dt.timestamp())
    run_at_iso = run_at_dt.isoformat()
    if not mark_delivery_once(int(user_id), str(kind or ""), "post_prompt_schedule", for_session(session_id)):
        return
    add_job(int(user_id), "post_prompt", run_at_iso, {"session_id": str(session_id), "run_at": int(run_at_epoch)})