from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from core.callback_utils import safe_answer_callback
from keyboards.inline import kb_micro_question, kb_sales_offer
from services.demo_analytics import demo_sent_kinds, record_demo_ack
from services.demo_policy import can_repeat_demo_for_user
from services.events import log_event
from services.jobs import add_job, cancel_jobs
from services.personalization import get_micro_question, should_offer_micro_question

router = Router()
UTC = ZoneInfo("UTC")


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


@router.callback_query(F.data.regexp(r"^demo:other:(work|home)$"))
async def demo_send_other(cb: CallbackQuery):
    """По кнопке под демо-аудио отправляем второй демо-транс.

    Требование UX:
    - отправляется только по запросу пользователя
    - без дубликатов (на всякий случай убираем старые demo_send)
    """
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return

    parts = (cb.data or "").split(":")
    if len(parts) != 3:
        return
    _, _, kind = parts
    if kind not in ("work", "home"):
        return

    user_id = int(cb.from_user.id)
    admin_demo_bypass = can_repeat_demo_for_user(user_id)

    sent = demo_sent_kinds(user_id)
    if not admin_demo_bypass and "work" in sent and "home" in sent:
        return await message.answer(
            "✅ Вы уже получили оба ресурсных демо-транса.\n\n"
            "Если Вы хотите продолжить — пожалуйста, оформите подписку.",
            reply_markup=kb_sales_offer(user_id),
        )
    if not admin_demo_bypass and kind in sent:
        return await message.answer(
            "✅ Этот демо-транс уже был отправлен Вам ранее.\n\n"
            "Если Вы хотите продолжить — пожалуйста, оформите подписку.",
            reply_markup=kb_sales_offer(user_id),
        )

    cancel_jobs(user_id, job_types=["demo_send"])

    run_now = datetime.now(UTC).replace(microsecond=0).isoformat()
    add_job(user_id, "demo_send", run_now, {"kind": kind, "src": "cross"})
    log_event(user_id, "demo_cross_requested", {"kind": kind})

    await message.answer("✅ Хорошо. Сейчас пришлю Вам второй ресурсный демо-транс.")


@router.callback_query(F.data.startswith("demo:ack:"))
async def demo_ack(cb: CallbackQuery):
    """Record legacy demo acknowledgement without deciding the sales path.

    The canonical PRE → audio → POST flow owns conversion planning after the
    user's outcome is known.  Keeping this legacy callback as acknowledgement
    only prevents it from becoming a second commercial decision authority.
    """

    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return

    parts = (cb.data or "").split(":")
    if len(parts) != 4:
        return await message.answer(
            "⚠️ Некорректная кнопка демо. Откройте меню → «Демо» и запланируйте демо заново."
        )

    _, _, kind, msgid = parts
    if kind not in ("work", "home"):
        return await message.answer(
            "⚠️ Некорректный тип демо. Откройте меню → «Демо» и запланируйте демо заново."
        )

    try:
        msg_id = int(msgid)
    except (TypeError, ValueError):
        logging.getLogger(__name__).exception("Unhandled exception")
        return await message.answer(
            "⚠️ Некорректный идентификатор демо. Откройте меню → «Демо» и запланируйте демо заново."
        )

    ack_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    ok = record_demo_ack(cb.from_user.id, kind, msg_id, ack_utc)

    if not ok:
        await message.answer(
            "Я не нашёл запись демо для этой кнопки.\n"
            "Это бывает, если сообщение переслали/удалили.\n\n"
            "Откройте меню → «Демо» и запланируйте демо заново."
        )
        return

    await message.answer(
        "✅ Спасибо! Я отметил, что Вы прослушали демо.\n\n"
        "Теперь сначала оцените своё состояние после практики в сообщении со шкалой. "
        "По этой оценке я покажу подходящий следующий шаг — без продажи вслепую."
    )
    log_event(
        cb.from_user.id,
        "trial_outcome_requested_after_ack",
        {"kind": kind, "message_id": msg_id},
    )
    log_event(
        cb.from_user.id,
        "trial_conversion_waiting_for_outcome",
        {"kind": kind, "message_id": msg_id},
    )

    try:
        q_key = should_offer_micro_question(int(cb.from_user.id))
        if q_key:
            q = get_micro_question(q_key)
            if q:
                await message.answer(
                    str(q.get("question")),
                    reply_markup=kb_micro_question(
                        str(q.get("key")),
                        list(q.get("options") or []),
                    ),
                )
    except sqlite3.Error:
        logging.getLogger(__name__).exception("Unhandled exception")
    except (KeyError, TypeError, ValueError):
        logging.getLogger(__name__).exception("Unhandled exception")
