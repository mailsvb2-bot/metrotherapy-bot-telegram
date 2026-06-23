import asyncio
import logging
from aiogram.exceptions import TelegramAPIError

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.admin import is_platform_admin
from services.db import db

router = Router()


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


def _users_count() -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


@router.message(Command("admin"))
async def admin_cmd(message: Message):
    uid = _message_user_id(message)
    if not is_platform_admin(uid):
        try:
            await message.answer("Недоступно.")
        except TelegramAPIError:
            logging.getLogger(__name__).exception("admin: failed to send deny message")
        return

    await message.answer(
        "🛠 Админ\n\n"
        "Команды:\n"
        "• /release — единый release/control-plane статус\n"
        "• /stats — базовая статистика\n"
        "• /users — количество пользователей\n"
        "• /state_last — последние состояния (лог)\n\n"
        "Подсказка: удобнее пользоваться кнопкой \"🛠 Панель\" в главном меню."
    )


@router.message(Command("users"))
async def users(message: Message):
    uid = _message_user_id(message)
    if not is_platform_admin(uid):
        return
    count = await asyncio.to_thread(_users_count)
    await message.answer(f"👤 Пользователей: {count}")
