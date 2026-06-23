from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from handlers.text_input_parts.common import is_marketing
from handlers.text_input_parts.states import MarketingCopyState
from keyboards.inline import kb_back_main
from services.ai_copywriter import generate_ab_texts
from services.funnel_copies import upsert_copy

router = Router()

def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


_STEP_TITLES = {
    "nudge": "мягкое напоминание",
    "postdemo": "сообщение после пробной практики",
    "offer": "предложение подписки",
    "offer_nextday": "предложение на следующий день",
    "deadline": "напоминание перед окончанием предложения",
    "lastcall": "последнее напоминание",
}

_ALLOWED_STEPS = set(_STEP_TITLES)


def _step_help() -> str:
    return "\n".join(f"• {key} — {title}" for key, title in _STEP_TITLES.items())


@router.message(MarketingCopyState.key)
async def msg_copy_key(message: Message, state: FSMContext):
    marketer_id = _message_user_id(message)
    if marketer_id is None or not is_marketing(marketer_id):
        await state.clear()
        return

    key = (message.text or "").strip().lower()
    if key not in _ALLOWED_STEPS:
        return await message.answer(
            "Не нашёл такой шаг. Отправьте одно слово из списка:\n\n" + _step_help(),
            reply_markup=kb_back_main(),
        )

    await state.update_data(copy_key=key)
    await state.set_state(MarketingCopyState.context)
    await message.answer(
        "Шаг 2 из 3. Расскажите, о чём должно быть сообщение.\n\n"
        "Например: «Человек прошёл пробную практику. Нужно мягко предложить продолжить с подпиской».\n\n"
        "Пишите обычными словами — я сам превращу это в готовый текст.",
        reply_markup=kb_back_main(),
    )


@router.message(MarketingCopyState.context)
async def msg_copy_context(message: Message, state: FSMContext):
    marketer_id = _message_user_id(message)
    if marketer_id is None or not is_marketing(marketer_id):
        await state.clear()
        return

    context = (message.text or "").strip()
    if len(context) < 10:
        return await message.answer("Напишите чуть подробнее, хотя бы одно короткое предложение.", reply_markup=kb_back_main())

    await state.update_data(copy_context=context)
    await state.set_state(MarketingCopyState.goal)
    await message.answer(
        "Шаг 3 из 3. Что человек должен сделать после этого сообщения?\n\n"
        "Например: «Открыть тарифы», «оформить подписку», «вернуться к практике завтра утром».\n\n"
        "Без сложных терминов — просто напишите нужное действие.",
        reply_markup=kb_back_main(),
    )


@router.message(MarketingCopyState.goal)
async def msg_copy_goal(message: Message, state: FSMContext):
    marketer_id = _message_user_id(message)
    if marketer_id is None or not is_marketing(marketer_id):
        await state.clear()
        return

    goal = (message.text or "").strip()
    if len(goal) < 5:
        return await message.answer("Пожалуйста, напишите цель чуть понятнее.", reply_markup=kb_back_main())

    data = await state.get_data()
    key = str(data.get("copy_key") or "offer").strip().lower()
    context = str(data.get("copy_context") or "").strip()

    a, b = await asyncio.to_thread(generate_ab_texts, context=context, goal=goal)
    upsert_copy(key, "A", a, created_by=marketer_id)
    upsert_copy(key, "B", b, created_by=marketer_id)

    await state.clear()

    step_title = _STEP_TITLES.get(key, key)
    await message.answer(
        "✅ Готово. Сохранил два варианта текста.\n\n"
        f"Для какого сообщения: {step_title}\n\n"
        "Первый вариант:\n" + (a[:600] + ("..." if len(a) > 600 else "")) + "\n\n"
        "Второй вариант:\n" + (b[:600] + ("..." if len(b) > 600 else "")) + "\n\n"
        "Бот будет использовать последнюю сохранённую версию. Вы можете в любой момент создать новые варианты.",
        reply_markup=kb_back_main(),
    )
