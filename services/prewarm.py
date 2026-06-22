from __future__ import annotations
from aiogram.exceptions import TelegramAPIError

from services.fast_send_audio import send_audio_cached

import asyncio
import time
import logging
from pathlib import Path
from typing import Iterable

from aiogram import Bot
from aiogram.types import FSInputFile

from config.settings import settings
from services.audio_cache import get_cached_file_id, save_cached_file_id
from services.catalog import AudioCatalog

log = logging.getLogger(__name__)

def _marker_path() -> Path:
    # local marker to avoid repeating prewarm on every restart
    return Path(__file__).resolve().parents[1] / ".prewarm_done"

def _already_done() -> bool:
    try:
        return _marker_path().exists()
    except OSError:
        logging.getLogger(__name__).exception("Unhandled exception")
        return False

def _mark_done() -> None:
    try:
        _marker_path().write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        logging.getLogger(__name__).exception("Unhandled exception")


def _admin_chat_id() -> int | None:
    raw_chat = (getattr(settings, "PREWARM_CHAT_ID", "") or "").strip()
    if raw_chat:
        try:
            return int(raw_chat)
        except OSError:
            logging.getLogger(__name__).exception("Unhandled exception")

    raw = (getattr(settings, "ADMIN_IDS", "") or "").strip()
    if not raw:
        return None
    # ADMIN_IDS can be "1,2,3"
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            return int(part)
        except OSError:
            logging.getLogger(__name__).exception("Unhandled exception")
            continue
    return None


async def prewarm_audio_cache(bot: Bot) -> None:
    """Pre-upload audio to Telegram to obtain file_id and make user sends instant.

    IMPORTANT: OFF by default. Enable explicitly with PREWARM_ENABLED=1.
    Also runs only once per machine (creates .prewarm_done marker).
    """
    if not getattr(settings, "PREWARM_ENABLED", False):
        return
    if _already_done():
        return
    chat_id = _admin_chat_id()
    if not chat_id:
        log.info("prewarm: ADMIN_IDS not set -> skip")
        return

    catalog = AudioCatalog()
    files: list[Path] = []
    try:
        files.extend(catalog.get_demo() or [])
    except OSError:
        logging.getLogger(__name__).exception("Unhandled exception")
    try:
        files.extend(catalog.get_full() or [])
    except OSError:
        logging.getLogger(__name__).exception("Unhandled exception")

    # de-dup while preserving order
    seen = set()
    uniq: list[Path] = []
    for p in files:
        rp = str(Path(p).resolve())
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(Path(p))

    for p in uniq:
        try:
            kind = "voice" if p.suffix.lower() in (".ogg", ".opus") else "audio"
            if get_cached_file_id(p, kind):
                continue

            if not p.exists():
                continue

            # Send quietly
            if kind == "voice":
                msg = await bot.send_voice(
                    chat_id,
                    voice=FSInputFile(p),
                    caption="(prewarm)",
                    disable_notification=True,
                    protect_content=True,
                )
                fid = getattr(getattr(msg, "voice", None), "file_id", None)
                if fid:
                    save_cached_file_id(p, "voice", str(fid))
            else:
                msg = await bot.send_audio(
                    chat_id,
                    audio=FSInputFile(p),
                    caption="(prewarm)",
                    disable_notification=True,
                    protect_content=True,
                )
                fid = getattr(getattr(msg, "audio", None), "file_id", None)
                if fid:
                    save_cached_file_id(p, "audio", str(fid))

            await asyncio.sleep(0.2)
        except (TelegramAPIError, OSError, asyncio.TimeoutError) as e:
            log.info("prewarm failed for %s: %s", str(p), e)
            continue

async def prewarm_matplotlib_cache() -> None:
    """Прогревает matplotlib font cache в фоне, чтобы не тормозить первый график."""
    try:
        from services.charts import _ensure_mpl
    except OSError:
        logging.getLogger(__name__).exception("prewarm_matplotlib_cache: import failed")
        return
    try:
        await asyncio.to_thread(_ensure_mpl)
        log.info("matplotlib cache prewarmed")
    except OSError:
        logging.getLogger(__name__).exception("prewarm_matplotlib_cache failed")