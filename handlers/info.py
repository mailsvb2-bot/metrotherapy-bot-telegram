from aiogram import Router
from aiogram.types import CallbackQuery

from keyboards.inline import kb_back_main

router = Router()

SUPPORT_TEXT = (
    "Если у Вас возникли вопросы — напишите в поддержку:\n"
    "@metrotherapysupportbot\n\n"
    "Если Telegram не открывает упоминание, можно перейти по ссылке:\n"
    "https://t.me/metrotherapysupportbot"
)

POLICY_URL = "https://t.me/metrotherapyprivacy"


@router.callback_query(lambda c: (c.data or "") == "info:support")
async def cb_support(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(SUPPORT_TEXT, reply_markup=kb_back_main())


@router.callback_query(lambda c: (c.data or "") == "info:policy")
async def cb_policy(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(
        f"🔐 Политика конфиденциальности:\n{POLICY_URL}",
        reply_markup=kb_back_main(),
    )
