from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.callback_utils import safe_answer_callback
from keyboards.inline import kb_mood_scale
from runtime.messenger_senders import TelegramBotSender
from services.messenger.audio_delivery import send_next_audio_to_user
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery
from services.payments.ui import kb_tariffs
from services.practice_journey import paid_route_summary, start_or_resume_paid_practice

router = Router()


def _callback_message(cb: CallbackQuery) -> Message | None:
    return cb.message if isinstance(cb.message, Message) else None


def _registry(bot: Bot) -> SenderRegistry:
    return SenderRegistry(telegram=TelegramBotSender(bot))


def _route_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎧 Продолжить маршрут", callback_data="practice:continue")],
            [InlineKeyboardButton(text="💳 Пакеты практик", callback_data="sub:menu")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings:menu")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
        ]
    )


@router.callback_query(F.data == "full")
async def full_access(cb: CallbackQuery) -> None:
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return

    user_id = int(cb.from_user.id)
    await message.answer(
        paid_route_summary(user_id),
        reply_markup=_route_keyboard(),
    )


@router.callback_query(F.data == "practice:continue")
async def continue_paid_practice(cb: CallbackQuery) -> None:
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    bot = cb.bot
    if message is None or bot is None:
        return

    user_id = int(cb.from_user.id)
    start = start_or_resume_paid_practice(user_id)

    if start.ready_for_pre_score:
        await message.answer(
            start.message,
            reply_markup=kb_mood_scale(int(start.session_id), stage="pre"),
        )
        return

    if start.status == "pending_audio":
        try:
            delivery = await send_next_audio_to_user(
                user_id,
                senders=_registry(bot),
                telegram_bot=bot,
                target_platform="telegram",
                fallback="telegram",
            )
        except UnsupportedMessengerDelivery:
            await message.answer("⚠️ Не удалось повторно отправить текущее аудио. Попробуйте ещё раз.")
            return
        await message.answer(delivery.message, reply_markup=_route_keyboard())
        return

    if start.status == "insufficient_balance":
        await message.answer(start.message, reply_markup=kb_tariffs(user_id))
        return

    await message.answer(start.message, reply_markup=_route_keyboard())
