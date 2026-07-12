from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config.settings import settings
from core.callback_utils import safe_answer_callback
from handlers.text_input_parts.common import add_job, parse_hhmm
from handlers.text_input_parts.states import InputState
from keyboards.inline import kb_back_main
from services.delivery_preferences import get_user_timezone
from services.events import log_event
from services.jobs import cancel_jobs

router = Router()
UTC = ZoneInfo("UTC")


def _callback_message(cb: CallbackQuery) -> Message | None:
    return cb.message if isinstance(cb.message, Message) else None


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return int(user.id) if user is not None else None


def _resolved_user_timezone(user_id: int) -> tuple[str, ZoneInfo]:
    candidates = (
        get_user_timezone(int(user_id)),
        getattr(settings, "TIMEZONE", "UTC"),
        "UTC",
    )
    for candidate in candidates:
        name = str(candidate or "").strip()
        if not name:
            continue
        try:
            return name, ZoneInfo(name)
        except ZoneInfoNotFoundError:
            continue
    return "UTC", UTC


@router.callback_query(lambda c: c.data in ("demo_kind_work", "demo_kind_home"))
async def pick_demo_kind(cb: CallbackQuery, state: FSMContext) -> None:
    await safe_answer_callback(cb)
    user_id = int(cb.from_user.id)
    kind = "work" if cb.data == "demo_kind_work" else "home"
    timezone_name, _ = _resolved_user_timezone(user_id)

    await state.update_data(demo_kind=kind)
    await state.set_state(InputState.demo_time)

    message = _callback_message(cb)
    if message is None:
        return
    await message.answer(
        "🕰 Укажите местное время, когда прислать демо-транс.\n\n"
        "Напишите в формате HH:MM (например, 08:30).\n\n"
        "Рекомендация: выберите время, когда Вы едете на работу или домой — "
        "так проще оценить практику в реальном ритме дня.\n\n"
        f"Часовой пояс: {timezone_name}.",
        reply_markup=kb_back_main(),
    )


@router.message(InputState.demo_time)
async def msg_demo_time(message: Message, state: FSMContext) -> None:
    parsed = parse_hhmm(message.text or "")
    if parsed is None:
        await message.answer(
            "Пожалуйста, напишите время в формате HH:MM (например, 08:30).",
            reply_markup=kb_back_main(),
        )
        return

    user_id = _message_user_id(message)
    if user_id is None:
        return
    hour, minute = parsed
    data = await state.get_data()
    kind = "home" if data.get("demo_kind") == "home" else "work"
    timezone_name, timezone = _resolved_user_timezone(user_id)

    now_local = datetime.now(timezone)
    send_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if send_local <= now_local:
        send_local += timedelta(days=1)
    send_utc = send_local.astimezone(UTC)
    now_utc = datetime.now(UTC)

    cancel_jobs(user_id, job_types=["demo_send", "demo_reminder"])
    add_job(user_id, "demo_send", send_utc.isoformat(), {"kind": kind})
    log_event(
        user_id,
        "demo_scheduled",
        {"kind": kind, "send_utc": send_utc.isoformat(), "timezone": timezone_name},
    )

    delta_sec = (send_utc - now_utc).total_seconds()
    if delta_sec > 5 * 60:
        remind_utc = send_utc - timedelta(minutes=5)
        add_job(user_id, "demo_reminder", remind_utc.isoformat(), {"kind": kind})
        log_event(
            user_id,
            "demo_reminder_scheduled",
            {"kind": kind, "remind_utc": remind_utc.isoformat(), "timezone": timezone_name},
        )

    await state.clear()
    confirmation = (
        f"✅ Отлично. Я пришлю демо-транс в {send_local.strftime('%H:%M')} "
        f"({timezone_name}).\n"
    )
    if delta_sec > 5 * 60:
        confirmation += "И мягко напомню за 5 минут до отправки.\n"
    await message.answer(confirmation, reply_markup=kb_back_main())
