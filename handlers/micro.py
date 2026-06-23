from __future__ import annotations
import logging
from aiogram.exceptions import TelegramAPIError


from aiogram import Router
from aiogram.types import CallbackQuery, Message

from services.personalization import get_micro_question, save_micro_answer

from core.callback_utils import safe_answer_callback
router = Router()


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


@router.callback_query(lambda c: (c.data or "").startswith("micro:"))
async def cb_micro_answer(cb: CallbackQuery):
    """Handle micro-question answers.

    callback_data: micro:<q_key>:<idx>
    """
    await safe_answer_callback(cb)
    data = (cb.data or "")
    parts = data.split(":", 2)
    if len(parts) != 3:
        return await safe_answer_callback(cb)

    _, q_key, idx_s = parts
    if not idx_s.isdigit():
        return await safe_answer_callback(cb)
    idx = int(idx_s)

    q = get_micro_question(q_key)
    if not q:
        return await safe_answer_callback(cb)

    options = q.get("options") or []
    if idx < 0 or idx >= len(options):
        return await safe_answer_callback(cb)

    answer = str(options[idx])
    save_micro_answer(int(cb.from_user.id), q_key, answer)

    # UX: коротко, без аналитики, только поддержка.
    await safe_answer_callback(cb, "✅ Спасибо.", show_alert=False)
    message = _callback_message(cb)
    if message is None:
        return
    try:
        await message.answer(
            "Спасибо. Возможно, сейчас Вам важно двигаться именно в таком темпе. Я подстроюсь под Ваш ритм."
        )
    except TelegramAPIError:
        logging.getLogger(__name__).exception("micro: send failed")
