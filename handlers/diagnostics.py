from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import settings
from services.messenger.setup import render_setup_text, render_setup_links_preview

router = Router()

@router.message(Command("whoami"))
async def cmd_whoami(message: Message):
    uid = message.from_user.id if message.from_user else None
    uname = (message.from_user.username or "").strip() if message.from_user else ""
    is_admin = bool(uid and uid in settings.admin_id_list)

    lines = []
    lines.append("🪪 Ваш идентификатор Telegram")
    lines.append(f"ID: <code>{uid}</code>" if uid is not None else "ID: неизвестен")
    if uname:
        lines.append(f"Username: @{uname}")
    lines.append("")
    lines.append(f"Статус: {'✅ Администратор' if is_admin else '👤 Пользователь'}")
    lines.append("")
    if is_admin:
        lines.append("Вы видите админ-панель и аналитику.")
    else:
        lines.append("Админ-панель и аналитика Вам не доступны.")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("messenger_setup"))
async def cmd_messenger_setup(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or uid not in settings.admin_id_list:
        await message.answer('Эта команда доступна только администратору.')
        return
    await message.answer(render_setup_text(), disable_web_page_preview=True)


@router.message(Command("messenger_links"))
async def cmd_messenger_links(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or uid not in settings.admin_id_list:
        await message.answer('Эта команда доступна только администратору.')
        return
    await message.answer(render_setup_links_preview(uid), disable_web_page_preview=True)
