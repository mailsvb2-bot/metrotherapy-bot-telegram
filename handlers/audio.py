from typing import cast

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, Message

from services.fast_send_audio import send_audio_cached
from services.bg import tm
from keyboards.inline import kb_full_access_menu
from services.audio_guard import get_full_files_guarded
from pathlib import Path

from core.callback_utils import safe_answer_callback
router = Router()


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


@router.callback_query(lambda c: c.data == "full")
async def full(cb: CallbackQuery):
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return

    user_id = int(cb.from_user.id)

    res = get_full_files_guarded(user_id)
    if not res.ok:
        await message.answer(res.message or "⚠️ Сейчас это недоступно.", reply_markup=kb_full_access_menu())
        return

    files = res.paths or []

    await message.answer("✅ Полный доступ активен. Треки:", reply_markup=kb_full_access_menu())

    # Отправку треков уводим в фон: клики должны ощущаться мгновенными,
    # а сеть/загрузка файлов могут занимать секунды.
    bot = cast(Bot, cb.bot)
    chat_id = int(message.chat.id)

    async def _send_all() -> None:
        import asyncio
        import logging
        from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter

        log = logging.getLogger(__name__)
        for f in files:
            try:
                await send_audio_cached(bot, chat_id, key=f"full:{Path(f).name}", file_path=str(f))
            except TelegramRetryAfter as e:
                # Telegram rate-limit: подождём и продолжим.
                await asyncio.sleep(float(getattr(e, "timeout", 1.0)))
                try:
                    await send_audio_cached(bot, chat_id, key=f"full:{Path(f).name}", file_path=str(f))
                except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError):
                    log.exception("Failed to send full track after RetryAfter")
                except OSError:
                    log.exception("Failed to send full track after RetryAfter")
                except ValueError:
                    log.exception("Failed to send full track after RetryAfter")
                except RuntimeError:
                    log.exception("Failed to send full track after RetryAfter")
            except (TelegramAPIError, TelegramBadRequest, TelegramNetworkError):
                log.exception("Failed to send full track")
            except OSError:
                log.exception("Failed to send full track")
            except ValueError:
                log.exception("Failed to send full track")
            except RuntimeError:
                log.exception("Failed to send full track")
            # небольшой yield, чтобы не забивать loop при длинных списках
            await asyncio.sleep(0)

    tm().create(_send_all())
