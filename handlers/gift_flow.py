from __future__ import annotations

import asyncio

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from services.gifts import get_gift_status, redeem_gift, activate_gift
from services.subscription import grant
from services.events import log_event
from handlers.payments import kb_after_paid  # reuse existing keyboard
from services.time_trace import mark as _mark_time  # optional

from core.callback_utils import safe_answer_callback
router = Router()

def _kb_intro(code: str) -> InlineKeyboardMarkup:
    """Клавиатура первого экрана подарка."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Как это работает", callback_data=f"gift:how:{code}")],
        [InlineKeyboardButton(text="Принять подарок", callback_data=f"gift:accept:{code}")],
        [InlineKeyboardButton(text="Выбрать время", callback_data=f"gift:time:{code}")],
    ])

def _kb_to_time(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Выбрать удобное время", callback_data=f"gift:time:{code}")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")],
    ])

GIFT_INTRO = (
    "🎁 *Вам подарили «Метротерапию»*\n\n"
    "Это подарок от человека, который хотел, чтобы в вашей дороге стало больше спокойствия и ясности.\n\n"
    "Метротерапия — это не медитация и не «занятие собой».\n"
    "Вы просто едете по своим делам — а в дороге с вами происходит работа.\n\n"
    "Это переобучение нервной системы через *ритм повседневности*."
)

GIFT_EXPLAIN = (
    "Как это работает:\n\n"
    "• вы *ничего не планируете*\n"
    "• вы *никуда специально не идёте*\n"
    "• вы *не «занимаетесь собой»*\n\n"
    "В привычное время дороги вам будет приходить аудио.\n"
    "Дорога остаётся дорогой — но состояние начинает меняться."
)


def _gift_intro_state(code: str, user_id: int) -> tuple[bool, str]:
    ok, msg, _gift = get_gift_status(code)
    if ok:
        log_event(int(user_id), "gift_intro_shown", {"code": code})
    return bool(ok), str(msg)


def _accept_gift_state(code: str, uid: int) -> tuple[bool, str, bool]:
    ok, msg, gift = get_gift_status(code)
    if not ok or not gift:
        return False, str(msg), False

    ok2, msg2, gift2 = redeem_gift(code, int(uid))
    if ok2 and gift2:
        grant(int(uid), gift2["scope"], gift2["days"])
        log_event(int(uid), "gift_accepted", {"code": code, "scope": gift2["scope"], "days": gift2["days"]})
        return True, str(msg2), True
    return False, str(msg2), True


def _accept_gift_for_time_state(code: str, uid: int) -> None:
    ok, _msg, gift = get_gift_status(code)
    if ok and gift:
        ok2, _, gift2 = redeem_gift(code, int(uid))
        if ok2 and gift2:
            grant(int(uid), gift2["scope"], gift2["days"])
            activate_gift(code, int(uid))
            log_event(int(uid), "gift_redeemed", {"code": code, "scope": gift2["scope"], "days": gift2["days"]})


async def send_gift_intro(message: Message, code: str) -> None:
    user_id = int(message.from_user.id) if message.from_user else 0
    ok, msg = await asyncio.to_thread(_gift_intro_state, code, user_id)
    if not ok:
        await message.answer(msg)
        return
    await message.answer(GIFT_INTRO, reply_markup=_kb_intro(code), parse_mode="Markdown")


@router.callback_query(F.data.startswith("gift:how:"))
async def gift_how(cb: CallbackQuery):
    await safe_answer_callback(cb)
    code = cb.data.split(":", 2)[2].strip()
    await cb.message.answer(GIFT_EXPLAIN, reply_markup=_kb_intro(code), parse_mode="Markdown")

@router.callback_query(F.data.startswith("gift:accept:"))
async def gift_accept(cb: CallbackQuery):
    await safe_answer_callback(cb)
    code = cb.data.split(":", 2)[2].strip()
    uid = int(cb.from_user.id)

    ok2, msg2, gift_known = await asyncio.to_thread(_accept_gift_state, code, uid)
    if ok2:
        # show explain + go time
        from aiogram.exceptions import TelegramBadRequest
        try:
            await cb.message.edit_text(GIFT_EXPLAIN, reply_markup=_kb_to_time(code), parse_mode="Markdown")
        except TelegramBadRequest:
            await cb.message.answer(GIFT_EXPLAIN, reply_markup=_kb_to_time(code), parse_mode="Markdown")
        return
    # if invalid or already activated
    await cb.message.answer(msg2)

@router.callback_query(F.data.startswith("gift:later:"))
async def gift_later(cb: CallbackQuery):
    await safe_answer_callback(cb)
    code = cb.data.split(":", 2)[2].strip()
    await cb.message.answer("Хорошо. Когда будете готовы — просто нажмите «Принять подарок» по этой ссылке снова.", reply_markup=None)

@router.callback_query(F.data.startswith("gift:time:"))
async def gift_time(cb: CallbackQuery):
    await safe_answer_callback(cb)
    code = cb.data.split(":", 2)[2].strip()
    uid = int(cb.from_user.id)

    # Если подарок ещё не принят — принимаем идемпотентно.
    await asyncio.to_thread(_accept_gift_for_time_state, code, uid)

    # После принятия — переиспользуем существующую клавиатуру настройки времени.
    await cb.message.answer(
        "✅ Подарок активирован.\n\n"
        "Чтобы всё работало идеально — назначьте удобное время получения утреннего и вечернего транса.",
        reply_markup=kb_after_paid(),
    )
