import logging
import sqlite3
from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from aiogram.filters import CommandStart

from services.messenger.entrypoints import register_user_entry

from handlers.menu import send_main_menu

router = Router()


@router.message(CommandStart())
async def start_cmd(message: Message):
    payload = ""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        payload = parts[1].strip()

    try:
        register_user_entry(
            message.from_user.id,
            platform='telegram',
            external_user_id=str(message.from_user.id),
            username=message.from_user.username,
            display_name=message.from_user.full_name,
            first_name=message.from_user.first_name,
            start_payload=payload,
        )
    except ValueError:
        logging.getLogger(__name__).exception("Bad start payload", extra={"payload": payload, "user_id": message.from_user.id})
        pass
    if payload.startswith("gift_"):
        code = payload.replace("gift_", "").strip()
        # Variant A: не активируем автоматически. Сначала — принятие подарка.
        try:
            from handlers.gift_flow import send_gift_intro
            await send_gift_intro(message, code)
        except ImportError:
            logging.getLogger(__name__).exception("gift_flow import failed")
            await message.answer("🎁 Вам подарили «Метротерапию». Откройте ссылку ещё раз.")
        except (sqlite3.Error, TelegramAPIError):
            logging.getLogger(__name__).exception("gift intro failed", extra={"code": code, "user_id": message.from_user.id})
            await message.answer("🎁 Вам подарили «Метротерапию». Откройте ссылку ещё раз.")
        except (ValueError, TypeError, AttributeError):
            logging.getLogger(__name__).exception("gift intro failed (unexpected)", extra={"code": code, "user_id": message.from_user.id})
            await message.answer("🎁 Вам подарили «Метротерапию». Откройте ссылку ещё раз.")
        await send_main_menu(message)
        return

    await send_main_menu(message)
