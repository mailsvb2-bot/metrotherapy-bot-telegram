from __future__ import annotations

from services.fast_send_audio import send_audio_cached

from pathlib import Path
from aiogram import Bot
from aiogram.types import FSInputFile


async def send_audio_fast(bot: Bot, chat_id: int, file_path: Path) -> None:
    """Send audio from disk (aiogram 3 compatible).

    Note: project primarily uses cached file_id paths for speed.
    This helper remains as a correct fallback and for future use.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    await bot.send_audio(chat_id, audio=FSInputFile(p))
