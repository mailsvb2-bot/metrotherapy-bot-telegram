from __future__ import annotations
from keyboards.inline import kb_back_main
from zoneinfo import ZoneInfo

import logging
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from handlers.text_input_parts.common import tzinfo, parse_hhmm, add_job
from handlers.text_input_parts.states import InputState
from services.events import log_event

from core.callback_utils import safe_answer_callback
router = Router()


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return int(user.id) if user is not None else None

@router.callback_query(lambda c: c.data in ("demo_kind_work", "demo_kind_home"))
async def pick_demo_kind(cb: CallbackQuery, state: FSMContext):
    await safe_answer_callback(cb)
    kind = "work" if cb.data == "demo_kind_work" else "home"

    await state.update_data(demo_kind=kind)
    await state.set_state(InputState.demo_time)

    text = (
        "🕰 Укажите время, когда прислать демо-транс.\n\n"
        "Напишите в формате HH:MM (например, 08:30).\n\n"
        "Рекомендация: выберите время, когда Вы едете на работу или домой — "
        "так Вы сможете максимально почувствовать эффект ресурсного аудиотранса: "
        "состояние становится легче и яснее, как после освежающего душа.\n\n"
        "Часовой пояс: Europe/Moscow."
    )
    message = _callback_message(cb)
    if message is None:
        return
    await message.answer(text, reply_markup=kb_back_main())




@router.message(InputState.demo_time)
async def msg_demo_time(message: Message, state: FSMContext):
    t = parse_hhmm(message.text or "")
    if not t:
        await message.answer("Пожалуйста, напишите время в формате HH:MM (например, 08:30).", reply_markup=kb_back_main())
        return

    h, m = t
    data = await state.get_data()
    kind = data.get("demo_kind", "work")
    user_id = _message_user_id(message)
    if user_id is None:
        return

    now_local = datetime.now(tzinfo())
    send_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)

    # если время уже прошло сегодня — переносим на завтра
    if send_local <= now_local:
        send_local = send_local + timedelta(days=1)

    send_utc = send_local.astimezone(ZoneInfo("UTC"))
    now_utc = datetime.now(ZoneInfo("UTC"))

    # ставим job на отправку демо
    add_job(user_id, "demo_send", send_utc.isoformat(), {"kind": kind})
    log_event(user_id, "demo_scheduled", {"kind": kind, "send_utc": send_utc.isoformat()})

    # напоминание за 5 минут — только если до отправки больше 5 минут
    delta_sec = (send_utc - now_utc).total_seconds()
    if delta_sec > 5 * 60:
        remind_utc = send_utc - timedelta(minutes=5)
        add_job(user_id, "demo_reminder", remind_utc.isoformat(), {"kind": kind})
        log_event(user_id, "demo_reminder_scheduled", {"kind": kind, "remind_utc": remind_utc.isoformat()})

    await state.clear()

    confirm = (
        f"✅ Отлично. Я пришлю демо-транс в {send_local.strftime('%H:%M')} (Europe/Moscow).\n"
    )
    if delta_sec > 5 * 60:
        confirm += "И мягко напомню за 5 минут до отправки.\n"
    await message.answer(confirm, reply_markup=kb_back_main())



