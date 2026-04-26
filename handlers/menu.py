from __future__ import annotations
import asyncio
import logging


from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext

from keyboards.inline import (
    kb_main,
    kb_back_main,
    kb_demo_kind,
)

from config.settings import settings
from services.db import db
from services.jobs import add_job, cancel_jobs
from services.personalization import get_preface, set_funnel_stage

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.callback_utils import safe_answer_callback
router = Router()


def _tz() -> ZoneInfo:
    return ZoneInfo(getattr(settings, "TIMEZONE", "Europe/Moscow"))


def _parse_hhmm(s: str) -> tuple[int, int] | None:
    s = (s or "").strip()
    if ":" not in s:
        return None
    hh, mm = s.split(":", 1)
    if not (hh.isdigit() and mm.isdigit()):
        return None
    h, m = int(hh), int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        return None
    return h, m


async def safe_edit(message: Message, text: str, reply_markup=None, parse_mode=None):
    """
    Убирает лаги/ошибки Telegram 'message is not modified'
    """
    try:
        # Нельзя edit_text у сообщений без текста (например, voice/audio/photo без caption).
        # В таких кейсах просто отправляем новое сообщение.
        if not (getattr(message, "text", None) or getattr(message, "caption", None)):
            await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return

        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        # TelegramBadRequest: there is no text in the message to edit
        if "no text in the message to edit" in str(e):
            await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        raise


def _is_admin(uid: int) -> bool:
    """Кнопка "Панель" видна ТОЛЬКО админам.

    Установка E/безопасность UX: обычные пользователи не должны видеть админ-кнопки.
    Установка A: не расширяем доступ через роли без явного ТЗ.
    """
    try:
        return int(uid) in set(settings.admin_id_list)
    except (TypeError, ValueError):
        logging.getLogger(__name__).exception("Unhandled exception")
        return False


async def send_main_menu(target: CallbackQuery | Message):
    user_id = target.from_user.id
    preface = await asyncio.to_thread(get_preface, int(user_id), "menu")
    text = (
        f"{preface}"
        "Главное меню\n\n"
        "Выберите, что Вас интересует:"
    )

    if isinstance(target, CallbackQuery):
        # Сразу подтверждаем нажатие, чтобы Telegram не показывал «ожидание»
        try:
            await target.answer()
        except (TelegramAPIError, TelegramBadRequest):
            logging.getLogger(__name__).debug("Callback answer failed", exc_info=True)

        await safe_edit(
            target.message,
            text,
            reply_markup=kb_main(user_id=target.from_user.id),
            parse_mode=None,
        )
    else:
        await target.answer(text, reply_markup=kb_main(user_id=target.from_user.id))


@router.callback_query(lambda c: c.data == "menu_main")
async def cb_menu_main(cb: CallbackQuery, state: FSMContext | None = None):
    await safe_answer_callback(cb)
    try:
        if state is not None:
            await state.clear()
    except (TypeError, ValueError, RuntimeError):
        logging.getLogger(__name__).debug("Failed to clear FSM state", exc_info=True)
    await send_main_menu(cb)


# Совместимость: в проекте встречается callback "menu:main"
@router.callback_query(lambda c: c.data == "menu:main")
async def cb_menu_main_v2(cb: CallbackQuery, state: FSMContext | None = None):
    await safe_answer_callback(cb)
    try:
        if state is not None:
            await state.clear()
    except (TypeError, ValueError, RuntimeError):
        logging.getLogger(__name__).debug("Failed to clear FSM state", exc_info=True)
    await send_main_menu(cb)


@router.callback_query(lambda c: c.data in ("demo_menu", "demo", "demo:menu"))
async def cb_demo_menu(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await asyncio.to_thread(set_funnel_stage, int(cb.from_user.id), "d0")
    preface = await asyncio.to_thread(get_preface, int(cb.from_user.id), "demo")
    text = (
        f"{preface}"
        "🎧 Демо\n\n"
        "Выберите, какой демо-транс Вам прислать:\n"
        "— «Дорога на работу»\n"
        "— «Дорога домой»\n\n"
        "После выбора я попрошу Вас указать время отправки."
    )
    await safe_edit(cb.message, text, reply_markup=kb_demo_kind(), parse_mode=None)


@router.callback_query(lambda c: c.data in ("back_main", "back"))
async def cb_back_main(cb: CallbackQuery, state: FSMContext | None = None):
    await safe_answer_callback(cb)
    try:
        if state is not None:
            await state.clear()
    except (TypeError, ValueError, RuntimeError):
        logging.getLogger(__name__).debug("Failed to clear FSM state", exc_info=True)
    await send_main_menu(cb)


@router.callback_query(lambda c: c.data == "remind:continue_tomorrow")
async def cb_remind_continue_tomorrow(cb: CallbackQuery):
    """Напоминание «продолжить завтра утром» для пользователей без подписки.

    UX не меняем: это дополнительная кнопка на экране «Полный доступ».
    Детерминированность: перед постановкой задачи удаляем предыдущие remind_*.
    """
    await safe_answer_callback(cb)

    user_id = int(cb.from_user.id)
    tz = _tz()

    # базовое время: work_time (если пользователь уже настраивал), иначе MORNING_TIME из настроек
    def _load_work_time_row() -> object:
        with db() as conn:
            return conn.execute("SELECT work_time FROM users WHERE user_id=?", (user_id,)).fetchone()

    row = await asyncio.to_thread(_load_work_time_row)
    hhmm = (row["work_time"] if row and row["work_time"] else "") or getattr(settings, "MORNING_TIME", "08:30")
    parsed = _parse_hhmm(str(hhmm)) or _parse_hhmm(getattr(settings, "MORNING_TIME", "08:30"))
    h, m = parsed if parsed else (8, 30)

    now_local = datetime.now(tz)
    run_local = (now_local + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
    run_utc = run_local.astimezone(ZoneInfo("UTC")).replace(microsecond=0).isoformat()

    # убираем старые напоминания этого типа
    await asyncio.to_thread(cancel_jobs, user_id, prefix="remind_")
    await asyncio.to_thread(add_job, user_id, "remind_continue", run_utc, {"src": "full_access", "hhmm": f"{h:02d}:{m:02d}"})

    tz_name = getattr(settings, "TIMEZONE", "Europe/Moscow")
    await cb.message.answer(
        f"✅ Хорошо. Я напомню Вам завтра в {run_local.strftime('%H:%M')} ({tz_name}).",
        reply_markup=kb_back_main(),
    )
