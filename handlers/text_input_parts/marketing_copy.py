from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from handlers.text_input_parts.common import is_marketing
from handlers.text_input_parts.states import MarketingCopyState
from keyboards.inline import kb_back_main
from services.ai_copywriter import generate_ab_texts
from services.funnel_copies import upsert_copy

router = Router()

@router.message(MarketingCopyState.key)
async def msg_copy_key(message: Message, state: FSMContext):
    if not is_marketing(message.from_user.id):
        await state.clear()
        return

    key = (message.text or "").strip().lower()
    allowed = {"nudge", "postdemo", "offer", "offer_nextday", "deadline", "lastcall"}
    if key not in allowed:
        return await message.answer(
            "Пожалуйста, укажите ключ шага воронки: nudge / postdemo / offer / offer_nextday / deadline / lastcall.",
            reply_markup=kb_back_main(),
        )

    await state.update_data(copy_key=key)
    await state.set_state(MarketingCopyState.context)
    await message.answer(
        "✍️ Теперь кратко опишите контекст продукта/оффера (1–5 предложений).\n\n"
        "Например: \"Метротерапия — аудиотрансы для дороги, подписка даёт ежедневные треки по расписанию\".",
        reply_markup=kb_back_main(),
    )




@router.message(MarketingCopyState.context)
async def msg_copy_context(message: Message, state: FSMContext):
    if not is_marketing(message.from_user.id):
        await state.clear()
        return

    context = (message.text or "").strip()
    if len(context) < 10:
        return await message.answer("Пожалуйста, чуть подробнее (минимум 10 символов).", reply_markup=kb_back_main())

    await state.update_data(copy_context=context)
    await state.set_state(MarketingCopyState.goal)
    await message.answer(
        "🎯 Какова цель сообщения?\n\n"
        "Например: \"Мягко предложить оплатить самый популярный тариф прямо сейчас\".",
        reply_markup=kb_back_main(),
    )




@router.message(MarketingCopyState.goal)
async def msg_copy_goal(message: Message, state: FSMContext):
    if not is_marketing(message.from_user.id):
        await state.clear()
        return

    goal = (message.text or "").strip()
    if len(goal) < 5:
        return await message.answer("Пожалуйста, сформулируйте цель чуть подробнее.", reply_markup=kb_back_main())

    data = await state.get_data()
    key = str(data.get("copy_key") or "offer").strip().lower()
    context = str(data.get("copy_context") or "").strip()

    a, b = generate_ab_texts(context=context, goal=goal)
    upsert_copy(key, "A", a, created_by=message.from_user.id)
    upsert_copy(key, "B", b, created_by=message.from_user.id)

    await state.clear()

    await message.answer(
        "✅ Готово. Я сохранил тексты для этого шага воронки в базе (A и B).\n\n"
        f"Ключ: {key}\n\n"
        "Вариант A (превью):\n" + (a[:600] + ("..." if len(a) > 600 else "")) + "\n\n"
        "Вариант B (превью):\n" + (b[:600] + ("..." if len(b) > 600 else "")) + "\n\n"
        "ℹ️ Дальше бот будет использовать эти тексты автоматически (последняя активная версия).",
        reply_markup=kb_back_main(),
    )



