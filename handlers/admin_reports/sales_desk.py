from __future__ import annotations

import asyncio
import logging
import sqlite3

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.callback_utils import safe_answer_callback
from handlers.admin_inline_common import AdminCtx, safe_edit
from handlers.admin_inline_states import AdminManageState
from handlers.admin_reports.sales_desk_inputs import (
    handle_message_input,
    handle_note_input,
)
from handlers.admin_reports.sales_desk_ui import (
    can_message,
    can_read,
    can_write,
    cancel_input_keyboard,
    card_keyboard,
    filter_from_callback,
    follow_days_from_callback,
    history_keyboard,
    lead_id_from_callback,
    overview_keyboard,
    stage_from_callback,
)
from services.sales_desk import (
    SalesDeskUnavailable,
    claim_lead,
    format_lead_card,
    format_lead_history,
    format_sales_overview,
    get_lead,
    sales_desk_snapshot,
    set_lead_stage,
    set_next_contact,
)

log = logging.getLogger(__name__)


async def _show_overview(
    cb: CallbackQuery,
    ctx: AdminCtx,
    filter_name: str,
) -> None:
    snapshot = await asyncio.to_thread(
        sales_desk_snapshot,
        filter_name=filter_name,
        admin_id=ctx.uid,
    )
    await safe_edit(
        cb,
        format_sales_overview(snapshot),
        reply_markup=overview_keyboard(snapshot),
    )


async def _show_card(cb: CallbackQuery, ctx: AdminCtx, lead_id: int) -> None:
    lead = await asyncio.to_thread(get_lead, int(lead_id))
    await safe_edit(
        cb,
        format_lead_card(lead),
        reply_markup=card_keyboard(
            lead,
            write_allowed=can_write(ctx),
            message_allowed=can_message(ctx),
        ),
    )


async def _show_error(cb: CallbackQuery, exc: BaseException) -> None:
    mapping = {
        "sales_lead_owned_by_another_admin": (
            "Лид уже назначен другому менеджеру."
        ),
        "sales_lead_not_found": "Лид не найден.",
        "sales_note_empty": "Заметка не может быть пустой.",
        "sales_message_empty": "Сообщение не может быть пустым.",
        "sales_telegram_identity_missing": (
            "У лида не найдена Telegram-идентичность."
        ),
        "sales_follow_up_closed_lead": (
            "Для закрытого лида нельзя назначить следующий контакт."
        ),
        "sales_lead_concurrent_update": (
            "Карточка уже изменилась. Откройте её заново."
        ),
    }
    text = mapping.get(str(exc), str(exc) or type(exc).__name__)
    await safe_answer_callback(cb, text, show_alert=True)


async def _begin_text_input(
    cb: CallbackQuery,
    state: FSMContext,
    *,
    lead_id: int,
    mode: str,
) -> None:
    message = cb.message
    if mode == "note":
        await state.set_state(AdminManageState.waiting_sales_note)
        prompt = (
            f"📝 Заметка для лида #{lead_id}\n\n"
            "Отправьте текст одним сообщением. "
            "Для отмены напишите «Отмена»."
        )
        callback_text = "Жду текст заметки."
    elif mode == "message":
        await state.set_state(AdminManageState.waiting_sales_message)
        prompt = (
            f"✉️ Сообщение лиду #{lead_id}\n\n"
            "Отправьте текст одним сообщением. Он будет доставлен "
            "в Telegram и записан в аудит. Для отмены напишите «Отмена»."
        )
        callback_text = "Жду текст сообщения."
    else:
        raise ValueError("invalid_sales_input_mode")

    await state.update_data(sales_lead_id=int(lead_id))
    if isinstance(message, Message):
        await message.answer(
            prompt,
            reply_markup=cancel_input_keyboard(lead_id),
        )
    await safe_answer_callback(
        cb,
        callback_text,
        show_alert=False,
    )


async def _run_read_action(
    cb: CallbackQuery,
    state: FSMContext,
    ctx: AdminCtx,
    data: str,
) -> bool:
    if data in {"admin:sales", "admin:sales:list"} or data.startswith(
        "admin:sales:list:"
    ):
        await _show_overview(cb, ctx, filter_from_callback(data))
        return True
    if data.startswith("admin:sales:lead:"):
        await _show_card(cb, ctx, lead_id_from_callback(data))
        return True
    if data.startswith("admin:sales:history:"):
        lead_id = lead_id_from_callback(data)
        lead = await asyncio.to_thread(get_lead, lead_id)
        await safe_edit(
            cb,
            format_lead_history(lead),
            reply_markup=history_keyboard(lead_id),
        )
        return True
    del state
    return False


async def _run_write_action(
    cb: CallbackQuery,
    state: FSMContext,
    ctx: AdminCtx,
    data: str,
) -> bool:
    lead_id = lead_id_from_callback(data)
    if data.startswith("admin:sales:claim:"):
        await asyncio.to_thread(
            claim_lead,
            lead_id=lead_id,
            actor_id=ctx.uid,
            force=ctx.is_superadmin,
        )
        await _show_card(cb, ctx, lead_id)
        return True
    if data.startswith("admin:sales:stage:"):
        await asyncio.to_thread(
            set_lead_stage,
            lead_id=lead_id,
            target_stage=stage_from_callback(data),
            actor_id=ctx.uid,
            force_owner=ctx.is_superadmin,
        )
        await _show_card(cb, ctx, lead_id)
        return True
    if data.startswith("admin:sales:follow:"):
        await asyncio.to_thread(
            set_next_contact,
            lead_id=lead_id,
            days=follow_days_from_callback(data),
            actor_id=ctx.uid,
            force_owner=ctx.is_superadmin,
        )
        await _show_card(cb, ctx, lead_id)
        return True
    if data.startswith("admin:sales:note:"):
        await _begin_text_input(
            cb,
            state,
            lead_id=lead_id,
            mode="note",
        )
        return True
    if data.startswith("admin:sales:message:"):
        if not can_message(ctx):
            await safe_answer_callback(
                cb,
                "Нужен отдельный доступ на сообщения лидам.",
                show_alert=True,
            )
            return True
        await _begin_text_input(
            cb,
            state,
            lead_id=lead_id,
            mode="message",
        )
        return True
    return False


async def run(
    cb: CallbackQuery,
    state: FSMContext,
    ctx: AdminCtx,
    handler_log,
) -> bool:
    del handler_log
    data = str(getattr(cb, "data", "") or "")
    if not (data == "admin:sales" or data.startswith("admin:sales:")):
        return False
    if not can_read(ctx):
        await safe_answer_callback(
            cb,
            "Нет доступа к Sales Desk.",
            show_alert=True,
        )
        return True

    input_prefixes = (
        "admin:sales:note:",
        "admin:sales:message:",
    )
    if not data.startswith(input_prefixes):
        await state.clear()

    try:
        if await _run_read_action(cb, state, ctx, data):
            return True
        if not can_write(ctx):
            await safe_answer_callback(
                cb,
                "Нужен отдельный доступ на изменение Sales Desk.",
                show_alert=True,
            )
            return True
        return await _run_write_action(cb, state, ctx, data)
    except PermissionError as exc:
        await _show_error(cb, exc)
        return True
    except ValueError as exc:
        await _show_error(cb, exc)
        return True
    except SalesDeskUnavailable as exc:
        await _show_error(cb, exc)
        return True
    except RuntimeError as exc:
        await _show_error(cb, exc)
        return True
    except OSError as exc:
        log.exception("Sales Desk admin action failed")
        await _show_error(cb, exc)
        return True
    except sqlite3.Error as exc:
        log.exception("Sales Desk database action failed")
        await _show_error(cb, exc)
        return True


__all__ = [
    "handle_message_input",
    "handle_note_input",
    "run",
]
