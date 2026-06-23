from __future__ import annotations
import logging

from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from handlers.text_input_parts.states import AdminInputState
from keyboards.inline import kb_back_main
from services.admin import is_admin
from services.admin_cards import user_card

router = Router()

def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


@router.message(AdminInputState.user_card)
async def msg_admin_user_card(message: Message, state: FSMContext):
    """Админ вводит user_id для карточки пользователя."""
    try:
        admin_id = _message_user_id(message)
    except AttributeError:
        logging.getLogger(__name__).exception("Admin check failed in user_card input")
        await state.clear()
        return

    if not is_admin(admin_id):
        await state.clear()
        return

    raw = (message.text or "").strip()
    if raw.startswith("@"):  # username в этой версии не ищем по API, только по БД
        raw = raw[1:]

    user_id = None
    if raw.isdigit():
        user_id = int(raw)

    if user_id is None:
        await message.answer("Пожалуйста, отправьте числовой user_id (например, 123456789).", reply_markup=kb_back_main())
        return

    c = user_card(user_id)
    await state.clear()

    if not c.get("user"):
        return await message.answer("❌ Пользователь не найден в базе.", reply_markup=kb_back_main())

    u = c["user"]
    sub = c.get("sub") or {}
    w = c.get("weather") or {}
    ref = c.get("ref") or {}
    demo = c.get("demo") or []
    beh = c.get("behavior") or {}
    micro = c.get("micro") or []

    demo_sent = {d.get("kind") for d in demo if d.get("sent_at_utc")}
    demo_acked = {d.get("kind") for d in demo if d.get("ack_at_utc")}

    username = (u.get("username") or "").strip()
    name = (u.get("first_name") or "").strip()
    head = f"👤 Карточка пользователя: {u.get('user_id')}"
    if username:
        head += f" (@{username})"
    if name:
        head += f" — {name}"

    lines = [
        head,
        f"• Зашёл: {u.get('joined_at') or '-'}",
        f"• Время: утро {u.get('work_time') or '-'} / вечер {u.get('home_time') or '-'}",
        f"• Демо: отправлено {int('work' in demo_sent)}/{int('home' in demo_sent)} | отмечено {int('work' in demo_acked)}/{int('home' in demo_acked)}",
        f"• Подписка: {sub.get('scope') or sub.get('plan_type') or '-'} | утро {sub.get('used_morning',0)}/{sub.get('total_morning',0)} | вечер {sub.get('used_evening',0)}/{sub.get('total_evening',0)} | {sub.get('status') or 'active'}",
        f"• Погода: город {w.get('city') or '-'}",
        f"• Реферал: реферер {ref.get('referrer_id') or '-'} | приглашено {c.get('invited_count') or 0}",
    ]

    if beh:
        prof = beh.get("profile") or "-"
        ema = beh.get("ema_delta_ms")
        dev = beh.get("ema_absdev_ms")
        lines.append(f"• Поведение: профиль {prof} | средний интервал ≈ {int(ema) if ema else '-'} мс | разброс ≈ {int(dev) if dev else '-'} мс")

    if micro:
        # показываем 3 последних ответа
        tail = []
        for r in micro[:3]:
            tail.append(f"{r.get('q_key')}: {r.get('answer')}")
        if tail:
            lines.append("• Микровопросы: " + " | ".join(tail))

    await message.answer("\n".join(lines), reply_markup=kb_back_main())



