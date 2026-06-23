from __future__ import annotations
import asyncio
import logging

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from config.settings import settings
from services.subscription import grant
from services.events import log_event
from services.db import db

router = Router()


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


def is_admin(uid: int) -> bool:
    return uid in settings.admin_id_list


def _reset_demo_storage(uid: int) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET demo_uses=0 WHERE user_id=?", (int(uid),))
        conn.execute("DELETE FROM demo_events WHERE user_id=?", (int(uid),))


@router.message(Command("grant"))
async def cmd_grant(message: Message):
    admin_id = _message_user_id(message)
    if admin_id is None or not is_admin(admin_id):
        return
    parts = (message.text or "").split()
    if len(parts) != 4:
        return await message.answer("Использование: /grant <user_id> <days> <morning|evening|both>")
    uid, days, scope = parts[1], parts[2], parts[3]
    if not uid.isdigit() or not days.isdigit() or scope not in ("morning", "evening", "both"):
        return await message.answer("Неверные параметры.")
    grant(int(uid), scope, int(days))
    log_event(int(uid), "admin_grant", {"by": admin_id, "days": int(days), "scope": scope})
    await message.answer("✅ Готово.")


@router.message(Command("reset_demo"))
async def cmd_reset_demo(message: Message):
    admin_id = _message_user_id(message)
    if admin_id is None or not is_admin(admin_id):
        return
    parts = (message.text or "").split()
    uid = admin_id
    if len(parts) == 2 and parts[1].isdigit():
        uid = int(parts[1])
    await asyncio.to_thread(_reset_demo_storage, int(uid))
    log_event(int(uid), "admin_reset_demo", {"by": admin_id})
    await message.answer("✅ Демо сброшено.")
