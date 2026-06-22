import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
import time
from config.settings import settings

router = Router()

START_TS = time.time()

@router.message(Command('admin_health'))
async def admin_health(msg: Message):
    uptime = int(time.time() - START_TS)
    text = (
        f"Version: {getattr(settings, 'VERSION', 'unknown')}\n"
        f"Uptime: {uptime}s\n"
    )
    await msg.answer(text)
