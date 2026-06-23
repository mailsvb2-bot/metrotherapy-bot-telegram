from __future__ import annotations

import logging
import sqlite3

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

from services.admin import is_platform_admin
from services.release_contract_report import format_runtime_contract_report
from services.release_control_report import format_release_control_report

router = Router()
_MAX_MESSAGE_CHARS = 3900


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


async def _answer(message: Message, text: str) -> None:
    try:
        await message.answer(text)
    except TelegramAPIError:
        logging.getLogger(__name__).exception("admin release: failed to send message")


async def _answer_chunks(message: Message, text: str) -> None:
    if len(text) <= _MAX_MESSAGE_CHARS:
        await _answer(message, text)
        return
    chunk: list[str] = []
    size = 0
    for line in text.splitlines():
        line_size = len(line) + 1
        if chunk and size + line_size > _MAX_MESSAGE_CHARS:
            await _answer(message, "\n".join(chunk))
            chunk = []
            size = 0
        chunk.append(line)
        size += line_size
    if chunk:
        await _answer(message, "\n".join(chunk))


@router.message(Command("release", "release_gate"))
async def release_control_cmd(message: Message) -> None:
    user_id = _message_user_id(message)
    if not is_platform_admin(user_id):
        await _answer(message, "Недоступно.")
        return
    try:
        report = format_release_control_report(limit=25) + "\n\n" + format_runtime_contract_report()
    except sqlite3.Error:
        logging.getLogger(__name__).exception("admin release: database error")
        await _answer(message, "🛑 Release/control отчёт недоступен: ошибка БД.")
        return
    except RuntimeError:
        logging.getLogger(__name__).exception("admin release: runtime error")
        await _answer(message, "🛑 Release/control отчёт недоступен: runtime error.")
        return
    except ValueError:
        logging.getLogger(__name__).exception("admin release: value error")
        await _answer(message, "🛑 Release/control отчёт недоступен: value error.")
        return
    await _answer_chunks(message, report)
