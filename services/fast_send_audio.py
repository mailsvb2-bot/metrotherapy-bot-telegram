from __future__ import annotations
import sqlite3
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError

import logging
from typing import Optional, Union
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from services.db import get_db
from services.safe_send import safe
from services.audio_cache import get_file_id, set_file_id

log = logging.getLogger(__name__)

async def send_audio_cached(bot: Bot, chat_id: int, key: str, file_path: Union[str, Path], caption: Optional[str] = None):
    """Send audio using cached Telegram file_id to keep latency < 1s after first send."""
    with get_db() as conn:
        fid = get_file_id(conn, key)

    async def _send_by_id():
        return await bot.send_audio(chat_id=chat_id, audio=fid, caption=caption)

    async def _send_by_file():
        msg = await bot.send_audio(chat_id=chat_id, audio=FSInputFile(str(file_path)), caption=caption)
        # cache file_id from Telegram response
        try:
            new_fid = msg.audio.file_id if msg.audio else None
            if new_fid:
                with get_db() as conn:
                    set_file_id(conn, key, new_fid)
                    conn.commit()
        except (sqlite3.Error, AttributeError, TypeError):
            log.exception("failed to cache file_id for %s", key)
        return msg

    if fid:
        try:
            return await safe(_send_by_id)
        except (TelegramBadRequest, TelegramAPIError):
            log.exception("Cached file_id failed for %s, falling back to upload", key)
            # fallback to file upload if cached id invalid
            return await safe(_send_by_file)
    else:
        return await safe(_send_by_file)