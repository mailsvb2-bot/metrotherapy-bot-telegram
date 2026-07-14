from __future__ import annotations

import asyncio
import logging
import sqlite3

from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers.admin_inline_common import AdminCtx
from handlers.admin_reports.sales_desk_ui import (
    can_message,
    can_read,
    can_write,
    input_done_keyboard,
)
from services.sales_desk import SalesDeskUnavailable, add_note
from services.sales_desk_contact import (
    mark_sales_message_failed,
    mark_sales_message_sent,
    prepare_sales_message,
)

log = logging.getLogger(__name__)


def _lead_id_from_state_data(state_data: dict[str, object]) -> int:
    try:
        return int(state_data.get("sales_lead_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("sales_lead_state_missing") from exc


def _is_cancel(text: str) -> bool:
    return text.lower() in {"отмена", "cancel", "/cancel"}


async def handle_note_input(
    msg: Message,
    state: FSMContext,
    ctx: AdminCtx,
) -> None:
    if not can_read(ctx) or not can_write(ctx):
        await state.clear()
        await msg.answer("Нет доступа на изменение Sales Desk.")
        return

    text = str(msg.text or "").strip()
    try:
        lead_id = _lead_id_from_state_data(await state.get_data())
    except ValueError:
        await state.clear()
        await msg.answer("Карточка лида потеряна. Откройте Sales Desk ещё раз.")
        return

    if _is_cancel(text):
        await state.clear()
        await msg.answer(
            "Заметка отменена.",
            reply_markup=input_done_keyboard(lead_id),
        )
        return

    try:
        await asyncio.to_thread(
            add_note,
            lead_id=lead_id,
            actor_id=ctx.uid,
            note_text=text,
            force_owner=ctx.is_superadmin,
        )
    except PermissionError:
        await state.clear()
        await msg.answer(
            "Лид уже назначен другому менеджеру.",
            reply_markup=input_done_keyboard(lead_id),
        )
        return
    except ValueError as exc:
        await msg.answer(str(exc))
        return
    except SalesDeskUnavailable:
        log.exception("Sales Desk note schema unavailable")
        await msg.answer(
            "Sales Desk ещё не готов на сервере. "
            "Обновите карточку после миграции."
        )
        return
    except RuntimeError:
        log.exception("Sales Desk note failed")
        await msg.answer(
            "Не удалось сохранить заметку. "
            "Попробуйте открыть карточку ещё раз."
        )
        return
    except OSError:
        log.exception("Sales Desk note failed")
        await msg.answer(
            "Не удалось сохранить заметку. "
            "Попробуйте открыть карточку ещё раз."
        )
        return
    except sqlite3.Error:
        log.exception("Sales Desk note failed")
        await msg.answer(
            "Не удалось сохранить заметку. "
            "Попробуйте открыть карточку ещё раз."
        )
        return

    await state.clear()
    await msg.answer(
        "✅ Заметка сохранена.",
        reply_markup=input_done_keyboard(lead_id),
    )


async def _mark_message_failed_best_effort(
    outbox_id: int,
    error_code: str,
) -> None:
    try:
        await asyncio.to_thread(
            mark_sales_message_failed,
            outbox_id=int(outbox_id),
            error_code=str(error_code),
        )
    except sqlite3.Error:
        log.exception("Sales Desk failed-message audit update failed")
    except RuntimeError:
        log.exception("Sales Desk failed-message audit update failed")
    except OSError:
        log.exception("Sales Desk failed-message audit update failed")
    except ValueError:
        log.exception("Sales Desk failed-message audit update failed")


async def _prepare_message(
    *,
    msg: Message,
    state: FSMContext,
    ctx: AdminCtx,
    lead_id: int,
    text: str,
) -> dict[str, object] | None:
    try:
        return await asyncio.to_thread(
            prepare_sales_message,
            lead_id=lead_id,
            actor_id=ctx.uid,
            message_text=text,
            force_owner=ctx.is_superadmin,
        )
    except PermissionError:
        await state.clear()
        await msg.answer(
            "Лид уже назначен другому менеджеру.",
            reply_markup=input_done_keyboard(lead_id),
        )
    except ValueError as exc:
        mapping = {
            "sales_message_empty": "Сообщение не может быть пустым.",
            "sales_telegram_identity_missing": (
                "У лида не найдена Telegram-идентичность."
            ),
        }
        await msg.answer(mapping.get(str(exc), str(exc)))
    except SalesDeskUnavailable:
        log.exception("Sales Desk message schema unavailable")
        await msg.answer(
            "Sales Desk ещё не готов на сервере. "
            "Обновите карточку после миграции."
        )
    except RuntimeError:
        log.exception("Sales Desk message preparation failed")
        await msg.answer("Не удалось подготовить сообщение.")
    except OSError:
        log.exception("Sales Desk message preparation failed")
        await msg.answer("Не удалось подготовить сообщение.")
    except sqlite3.Error:
        log.exception("Sales Desk message preparation failed")
        await msg.answer("Не удалось подготовить сообщение.")
    return None


async def _send_prepared_message(
    *,
    msg: Message,
    state: FSMContext,
    lead_id: int,
    prepared: dict[str, object],
) -> object | None:
    outbox_id = int(prepared["outbox_id"])
    try:
        return await msg.bot.send_message(
            chat_id=int(prepared["chat_id"]),
            text=str(prepared["message_text"]),
        )
    except TelegramAPIError as exc:
        await _mark_message_failed_best_effort(outbox_id, type(exc).__name__)
        await state.clear()
        await msg.answer(
            "Telegram не подтвердил доставку сообщения. "
            "Результат записан в историю лида; не отправляйте повторно "
            "без проверки.",
            reply_markup=input_done_keyboard(lead_id),
        )
    except asyncio.TimeoutError:
        await _mark_message_failed_best_effort(outbox_id, "TimeoutError")
        await state.clear()
        await msg.answer(
            "Статус доставки неизвестен из-за таймаута. "
            "Не отправляйте сообщение повторно без проверки истории лида.",
            reply_markup=input_done_keyboard(lead_id),
        )
    except OSError as exc:
        await _mark_message_failed_best_effort(outbox_id, type(exc).__name__)
        await state.clear()
        await msg.answer(
            "Статус доставки неизвестен из-за сетевой ошибки. "
            "Не отправляйте сообщение повторно без проверки истории лида.",
            reply_markup=input_done_keyboard(lead_id),
        )
    return None


async def _finalize_sent_message(
    *,
    msg: Message,
    state: FSMContext,
    lead_id: int,
    outbox_id: int,
    provider_message_id: int,
) -> bool:
    try:
        await asyncio.to_thread(
            mark_sales_message_sent,
            outbox_id=int(outbox_id),
            provider_message_id=int(provider_message_id),
        )
    except sqlite3.Error:
        log.exception("Sales Desk sent-message audit finalization failed")
    except RuntimeError:
        log.exception("Sales Desk sent-message audit finalization failed")
    except OSError:
        log.exception("Sales Desk sent-message audit finalization failed")
    except ValueError:
        log.exception("Sales Desk sent-message audit finalization failed")
    else:
        return True

    await state.clear()
    await msg.answer(
        "Сообщение отправлено, но журнал не подтвердил запись. "
        "Не отправляйте его повторно.",
        reply_markup=input_done_keyboard(lead_id),
    )
    return False


async def handle_message_input(
    msg: Message,
    state: FSMContext,
    ctx: AdminCtx,
) -> None:
    if not can_read(ctx) or not can_message(ctx):
        await state.clear()
        await msg.answer("Нет доступа на сообщения лидам.")
        return

    text = str(msg.text or "").strip()
    try:
        lead_id = _lead_id_from_state_data(await state.get_data())
    except ValueError:
        await state.clear()
        await msg.answer("Карточка лида потеряна. Откройте Sales Desk ещё раз.")
        return

    if _is_cancel(text):
        await state.clear()
        await msg.answer(
            "Сообщение отменено.",
            reply_markup=input_done_keyboard(lead_id),
        )
        return

    prepared = await _prepare_message(
        msg=msg,
        state=state,
        ctx=ctx,
        lead_id=lead_id,
        text=text,
    )
    if prepared is None:
        return

    delivered = await _send_prepared_message(
        msg=msg,
        state=state,
        lead_id=lead_id,
        prepared=prepared,
    )
    if delivered is None:
        return

    finalized = await _finalize_sent_message(
        msg=msg,
        state=state,
        lead_id=lead_id,
        outbox_id=int(prepared["outbox_id"]),
        provider_message_id=int(getattr(delivered, "message_id")),
    )
    if not finalized:
        return

    await state.clear()
    await msg.answer(
        "✅ Сообщение отправлено и записано в историю лида.",
        reply_markup=input_done_keyboard(lead_id),
    )
