from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from core.callback_utils import safe_answer_callback
from keyboards.inline import kb_back_main
from services.privacy_controls import erase_user_behavioral_data, export_user_data_snapshot

router = Router()
log = logging.getLogger(__name__)

SUPPORT_TEXT = (
    "Если у Вас возникли вопросы — напишите в поддержку:\n"
    "@metrotherapysupportbot\n\n"
    "Если Telegram не открывает упоминание, можно перейти по ссылке:\n"
    "https://t.me/metrotherapysupportbot"
)

POLICY_URL = "https://t.me/metrotherapyprivacy"


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return int(user.id) if user is not None else None


def _delete_confirmed(text: str | None) -> bool:
    parts = str(text or "").strip().split(maxsplit=1)
    return len(parts) == 2 and parts[1].strip().upper() == "CONFIRM"


@router.callback_query(lambda c: (c.data or "") == "info:support")
async def cb_support(cb: CallbackQuery):
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return
    await message.answer(SUPPORT_TEXT, reply_markup=kb_back_main())


@router.callback_query(lambda c: (c.data or "") == "info:policy")
async def cb_policy(cb: CallbackQuery):
    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return
    await message.answer(
        f"🔐 Политика конфиденциальности:\n{POLICY_URL}\n\n"
        "Получить копию своих данных: /mydata\n"
        "Удалить поведенческие данные: /deletemydata",
        reply_markup=kb_back_main(),
    )


@router.message(Command("mydata"))
async def cmd_my_data(message: Message) -> None:
    user_id = _message_user_id(message)
    if user_id is None:
        return
    try:
        snapshot = await asyncio.to_thread(export_user_data_snapshot, user_id)
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        document = BufferedInputFile(payload, filename=f"metrotherapy-user-data-{user_id}.json")
        await message.answer_document(
            document,
            caption=(
                "🔐 Это экспорт данных, связанных с Вашим аккаунтом. "
                "Файл может содержать историю использования и платёжные записи — храните его безопасно."
            ),
        )
    except (sqlite3.Error, RuntimeError, ValueError, TypeError, OSError):
        log.exception("User data export failed: user_id=%s", user_id)
        await message.answer(
            "Не удалось подготовить экспорт данных. Повторите позже или напишите в поддержку: "
            "@metrotherapysupportbot"
        )


@router.message(Command("deletemydata"))
async def cmd_delete_my_data(message: Message) -> None:
    user_id = _message_user_id(message)
    if user_id is None:
        return
    if not _delete_confirmed(message.text):
        await message.answer(
            "⚠️ Команда удалит поведенческую историю и обезличит профиль. "
            "Платёжные, возвратные и иные обязательные учётные записи сохраняются.\n\n"
            "Для подтверждения отправьте точно:\n"
            "/deletemydata CONFIRM"
        )
        return

    try:
        result = await asyncio.to_thread(
            erase_user_behavioral_data,
            user_id,
            reason="telegram_user_request",
        )
    except (sqlite3.Error, RuntimeError, ValueError, TypeError):
        log.exception("User data erasure failed: user_id=%s", user_id)
        await message.answer(
            "Не удалось выполнить удаление данных. Повторите позже или напишите в поддержку: "
            "@metrotherapysupportbot"
        )
        return

    deleted_rows = sum(int(value) for value in result.deleted_tables.values())
    await message.answer(
        "✅ Поведенческие данные удалены, профиль обезличен.\n"
        f"Удалено записей: {deleted_rows}.\n"
        "Платёжные и другие обязательные учётные записи сохранены в обезличенном виде или по требованиям учёта."
    )
