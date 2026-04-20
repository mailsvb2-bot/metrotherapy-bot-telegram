from services.fast_send_audio import send_audio_cached
from services.bg import tm
from aiogram import Router
from aiogram.types import CallbackQuery

from keyboards.inline import kb_full_access_menu
from services.audio_guard import get_full_files_guarded
from pathlib import Path

router = Router()

@router.callback_query(lambda c: c.data == "full")
async def full(cb: CallbackQuery):
    await cb.answer()
    user_id = cb.from_user.id

    res = get_full_files_guarded(user_id)
    if not res.ok:
        await cb.message.answer(res.message or "⚠️ Сейчас это недоступно.", reply_markup=kb_full_access_menu())
        return

    files = res.paths or []

    await cb.message.answer("✅ Полный доступ активен. Треки:", reply_markup=kb_full_access_menu())

    # Отправку треков уводим в фон: клики должны ощущаться мгновенными,
    # а сеть/загрузка файлов могут занимать секунды.
    bot = cb.bot
    chat_id = int(cb.message.chat.id)

    async def _send_all() -> None:
        import asyncio
        import logging
        from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError, RetryAfter

        log = logging.getLogger(__name__)
        for f in files:
            try:
                await send_audio_cached(bot, chat_id, key=f"full:{Path(f).name}", file_path=str(f))
            except RetryAfter as e:
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
