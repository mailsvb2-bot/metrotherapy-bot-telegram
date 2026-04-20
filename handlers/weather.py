from __future__ import annotations
import logging


from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

import asyncio

from keyboards.inline import kb_back_main, kb_main, kb_weather
from services.weather import get_weather_text_async, set_location, set_city
from services.pending import set_pending, pop_pending, peek_pending
from services.events import log_event
from config.settings import settings


router = Router()


@router.callback_query(F.data == "weather:show")
async def weather_show(cb: CallbackQuery):
    txt = await get_weather_text_async(int(cb.from_user.id))
    await cb.message.edit_text(
        txt + "\n\nВы можете отправить геолокацию или указать город вручную.",
        reply_markup=kb_weather(),
    )


@router.callback_query(F.data == "weather:city")
async def weather_city(cb: CallbackQuery, state: FSMContext):
    # Если пользователь был в другом сценарии со state (например, ввод времени),
    # сбрасываем state, чтобы ввод города не перехватился чужим обработчиком.
    try:
        await state.clear()
    except (TelegramAPIError, TelegramBadRequest):
        logging.getLogger(__name__).debug("Callback answer failed", exc_info=True)
    set_pending(cb.from_user.id, "weather_city", {})
    await cb.message.answer(
        "🏙 Пожалуйста, напишите название города (например: «Казань»).\n\n"
        "Город можно будет изменить в любой момент.",
        reply_markup=kb_back_main(),
    )


@router.message(F.location)
async def weather_location(message: Message):
    loc = message.location
    if not loc:
        return
    set_location(int(message.from_user.id), float(loc.latitude), float(loc.longitude))
    await message.answer(
        "✅ Спасибо! Я сохранил Вашу локацию. Теперь погода будет точнее.\n\n"
        + (await get_weather_text_async(int(message.from_user.id))),
        reply_markup=kb_main(user_id=message.from_user.id),
    )


@router.message(F.text)
async def weather_city_input(message: Message):
    """Приём текста города после команды/кнопки погоды.

    Без FSM: используем pending-хранилище (детерминированно и компактно).
    """
    # Диагностика конфликтов ввода HH:MM
    try:
        from services.time_trace import mark as _mark_time
        _mark_time("handlers.weather:weather_city_input")
    except (ImportError, AttributeError):
        logging.getLogger(__name__).debug("time_trace unavailable", exc_info=True)

    uid = int(message.from_user.id)
    p = peek_pending(uid)
    if not p or p.kind != "weather_city":
        raise SkipHandler

    # фиксируем ввод, даже если дальше будет ошибка поиска
    pop_pending(uid)

    city_raw = (message.text or "").strip()
    if not city_raw:
        return await message.answer("Пожалуйста, напишите название города текстом.")

    ok, info = await asyncio.to_thread(set_city, uid, city_raw)
    if not ok:
        return await message.answer("❌ " + str(info), reply_markup=kb_back_main())

    log_event(uid, "weather_city_set", {"city": str(info)})
    txt = await get_weather_text_async(uid, timeout_sec=1.5)
    await message.answer(
        f"✅ Город принят: {info}.\n\n{txt}\n\n"
        "Если Вы захотите изменить город — нажмите «Погода» и выберите «Изменить город».",
        reply_markup=kb_main(user_id=message.from_user.id),
    )