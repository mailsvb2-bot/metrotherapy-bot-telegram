from aiogram import Router
from aiogram.types import CallbackQuery, Message

from keyboards.inline import kb_back_main

from core.callback_utils import safe_answer_callback
router = Router()

SUPPORT_TEXT = (
    "Если у Вас возникли вопросы — напишите в поддержку:\n"
    "@metrotherapysupportbot\n\n"
    "Если Telegram не открывает упоминание, можно перейти по ссылке:\n"
    "https://t.me/metrotherapysupportbot"
)

POLICY_URL = "https://t.me/metrotherapyprivacy"


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


@router.callback_query(lambda c: (c.data or "") == "info:support")
async def cb_support(cb: CallbackQuery):
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return
    await message.answer(SUPPORT_TEXT, reply_markup=kb_back_main())


@router.callback_query(lambda c: (c.data or "") == "info:policy")
async def cb_policy(cb: CallbackQuery):
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return
    await message.answer(
        f"🔐 Политика конфиденциальности:\n{POLICY_URL}",
        reply_markup=kb_back_main(),
    )
