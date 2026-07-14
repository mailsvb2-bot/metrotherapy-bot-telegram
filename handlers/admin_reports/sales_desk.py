from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.callback_utils import safe_answer_callback
from handlers.admin_inline_common import AdminCtx, safe_edit
from handlers.admin_inline_states import AdminManageState
from services.admin_permissions import SALES_DESK_PERMISSION, SALES_WRITE_PERMISSION
from services.sales_desk import (
    SalesDeskUnavailable,
    add_note,
    claim_lead,
    format_lead_card,
    format_lead_history,
    format_sales_overview,
    get_lead,
    sales_desk_snapshot,
    set_lead_stage,
    set_next_contact,
)
from services.sales_desk_core import SALES_STAGES, can_transition, normalize_filter

log = logging.getLogger(__name__)

_FILTER_LABELS = (
    ("open", "Открытые"),
    ("overdue", "Просроченные"),
    ("mine", "Мои"),
    ("unassigned", "Без ответственного"),
    ("new", "Новые"),
    ("contacted", "Связались"),
    ("qualified", "Заинтересованы"),
    ("checkout", "Оплата"),
    ("won", "Оплатили"),
    ("lost", "Отказы"),
)

_STAGE_BUTTONS = {
    "new": "Новый",
    "contacted": "Связались",
    "qualified": "Заинтересован",
    "checkout": "Начал оплату",
    "won": "Оплатил",
    "lost": "Отказ",
}


def _can_read(ctx: AdminCtx) -> bool:
    if ctx.is_superadmin:
        return True
    if ctx.allowed_perms is None:
        return True
    return SALES_DESK_PERMISSION in ctx.allowed_perms


def _can_write(ctx: AdminCtx) -> bool:
    if ctx.is_superadmin:
        return True
    return ctx.allowed_perms is not None and SALES_WRITE_PERMISSION in ctx.allowed_perms


def _parts(data: str | None) -> list[str]:
    return [part for part in str(data or "").split(":") if part]


def _lead_id(data: str | None) -> int:
    parts = _parts(data)
    try:
        return int(parts[-1])
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError("sales_lead_id_missing") from exc


def _filter_from_data(data: str | None) -> str:
    parts = _parts(data)
    if "list" not in parts:
        return "open"
    index = parts.index("list") + 1
    return normalize_filter(parts[index] if index < len(parts) else "open")


def _stage_from_data(data: str | None) -> str:
    parts = _parts(data)
    if "stage" not in parts:
        raise ValueError("sales_stage_missing")
    index = parts.index("stage") + 1
    if index >= len(parts) or parts[index] not in SALES_STAGES:
        raise ValueError("invalid_sales_stage")
    return parts[index]


def _follow_days(data: str | None) -> int | None:
    parts = _parts(data)
    if "follow" not in parts:
        raise ValueError("sales_follow_up_missing")
    index = parts.index("follow") + 1
    if index >= len(parts):
        raise ValueError("sales_follow_up_missing")
    value = parts[index]
    if value == "clear":
        return None
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_sales_follow_up_days") from exc
    if days not in {1, 3, 7}:
        raise ValueError("invalid_sales_follow_up_days")
    return days


def _home_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")]


def _overview_kb(snapshot: dict[str, Any]) -> InlineKeyboardMarkup:
    selected = normalize_filter(str(snapshot.get("filter") or "open"))
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(_FILTER_LABELS), 2):
        row: list[InlineKeyboardButton] = []
        for key, title in _FILTER_LABELS[index:index + 2]:
            prefix = "✅ " if key == selected else ""
            row.append(
                InlineKeyboardButton(
                    text=f"{prefix}{title}",
                    callback_data=f"admin:sales:list:{key}",
                )
            )
        rows.append(row)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for lead in list(snapshot.get("leads") or [])[:12]:
        lead_id = int(lead.get("id") or 0)
        stage = str(lead.get("stage") or "new")
        owner = lead.get("assigned_to")
        overdue = bool(
            lead.get("next_contact_at")
            and str(lead.get("next_contact_at")) < now
            and stage not in {"won", "lost"}
        )
        marker = "⏰ " if overdue else ""
        owner_text = f" · {owner}" if owner is not None else " · без ответственного"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}#{lead_id} {str(lead.get('display_name') or 'Лид')[:28]}{owner_text}",
                    callback_data=f"admin:sales:lead:{lead_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:sales:list:{selected}")])
    rows.append([InlineKeyboardButton(text="🤖 Growth Autopilot", callback_data="admin:growth:autopilot")])
    rows.append(_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _card_kb(lead: dict[str, Any], *, can_write: bool) -> InlineKeyboardMarkup:
    lead_id = int(lead.get("id") or 0)
    current = str(lead.get("stage") or "new")
    rows: list[list[InlineKeyboardButton]] = []
    if can_write:
        rows.append(
            [
                InlineKeyboardButton(text="🙋 Взять лид", callback_data=f"admin:sales:claim:{lead_id}"),
                InlineKeyboardButton(text="📝 Заметка", callback_data=f"admin:sales:note:{lead_id}"),
            ]
        )
        stage_buttons = [
            InlineKeyboardButton(
                text=_STAGE_BUTTONS[target],
                callback_data=f"admin:sales:stage:{target}:{lead_id}",
            )
            for target in SALES_STAGES
            if target != current and can_transition(current, target)
        ]
        for index in range(0, len(stage_buttons), 2):
            rows.append(stage_buttons[index:index + 2])
        if current not in {"won", "lost"}:
            rows.append(
                [
                    InlineKeyboardButton(text="⏰ +1 день", callback_data=f"admin:sales:follow:1:{lead_id}"),
                    InlineKeyboardButton(text="⏰ +3 дня", callback_data=f"admin:sales:follow:3:{lead_id}"),
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(text="⏰ +7 дней", callback_data=f"admin:sales:follow:7:{lead_id}"),
                    InlineKeyboardButton(text="Убрать follow-up", callback_data=f"admin:sales:follow:clear:{lead_id}"),
                ]
            )
    rows.append([InlineKeyboardButton(text="🧾 История", callback_data=f"admin:sales:history:{lead_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ К лидам", callback_data="admin:sales:list:open")])
    rows.append(_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _history_kb(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К карточке", callback_data=f"admin:sales:lead:{int(lead_id)}")],
            [InlineKeyboardButton(text="⬅️ К лидам", callback_data="admin:sales:list:open")],
            _home_row(),
        ]
    )


def _note_done_kb(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть карточку", callback_data=f"admin:sales:lead:{int(lead_id)}")],
            [InlineKeyboardButton(text="⬅️ К лидам", callback_data="admin:sales:list:open")],
            _home_row(),
        ]
    )


async def _show_overview(cb: CallbackQuery, ctx: AdminCtx, filter_name: str) -> None:
    snapshot = await asyncio.to_thread(
        sales_desk_snapshot,
        filter_name=filter_name,
        admin_id=ctx.uid,
    )
    await safe_edit(cb, format_sales_overview(snapshot), reply_markup=_overview_kb(snapshot))


async def _show_card(cb: CallbackQuery, ctx: AdminCtx, lead_id: int) -> None:
    lead = await asyncio.to_thread(get_lead, int(lead_id))
    await safe_edit(cb, format_lead_card(lead), reply_markup=_card_kb(lead, can_write=_can_write(ctx)))


async def _show_error(cb: CallbackQuery, exc: BaseException) -> None:
    mapping = {
        "sales_lead_owned_by_another_admin": "Лид уже назначен другому менеджеру.",
        "sales_lead_not_found": "Лид не найден.",
        "sales_note_empty": "Заметка не может быть пустой.",
    }
    text = mapping.get(str(exc), str(exc) or type(exc).__name__)
    await safe_answer_callback(cb, text, show_alert=True)


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, handler_log) -> bool:
    del handler_log
    data = str(getattr(cb, "data", "") or "")
    if not (data == "admin:sales" or data.startswith("admin:sales:")):
        return False
    if not _can_read(ctx):
        await safe_answer_callback(cb, "Нет доступа к Sales Desk.", show_alert=True)
        return True

    if not data.startswith("admin:sales:note:"):
        await state.clear()

    try:
        if data in {"admin:sales", "admin:sales:list"} or data.startswith("admin:sales:list:"):
            await _show_overview(cb, ctx, _filter_from_data(data))
            return True
        if data.startswith("admin:sales:lead:"):
            await _show_card(cb, ctx, _lead_id(data))
            return True
        if data.startswith("admin:sales:history:"):
            lead_id = _lead_id(data)
            lead = await asyncio.to_thread(get_lead, lead_id)
            await safe_edit(cb, format_lead_history(lead), reply_markup=_history_kb(lead_id))
            return True

        if not _can_write(ctx):
            await safe_answer_callback(
                cb,
                "Нужен отдельный доступ на изменение Sales Desk.",
                show_alert=True,
            )
            return True

        lead_id = _lead_id(data)
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
                target_stage=_stage_from_data(data),
                actor_id=ctx.uid,
                force_owner=ctx.is_superadmin,
            )
            await _show_card(cb, ctx, lead_id)
            return True
        if data.startswith("admin:sales:follow:"):
            await asyncio.to_thread(
                set_next_contact,
                lead_id=lead_id,
                days=_follow_days(data),
                actor_id=ctx.uid,
                force_owner=ctx.is_superadmin,
            )
            await _show_card(cb, ctx, lead_id)
            return True
        if data.startswith("admin:sales:note:"):
            await state.set_state(AdminManageState.waiting_sales_note)
            await state.update_data(sales_lead_id=lead_id)
            message = cb.message
            if isinstance(message, Message):
                await message.answer(
                    f"📝 Заметка для лида #{lead_id}\n\nОтправьте текст одним сообщением. Для отмены напишите «Отмена».",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="Отмена", callback_data=f"admin:sales:lead:{lead_id}")]
                        ]
                    ),
                )
            await safe_answer_callback(cb, "Жду текст заметки.", show_alert=False)
            return True
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
    return False


async def handle_note_input(msg: Message, state: FSMContext, ctx: AdminCtx) -> None:
    if not _can_read(ctx) or not _can_write(ctx):
        await state.clear()
        await msg.answer("Нет доступа на изменение Sales Desk.")
        return

    text = str(msg.text or "").strip()
    state_data = await state.get_data()
    try:
        lead_id = int(state_data.get("sales_lead_id"))
    except (TypeError, ValueError):
        await state.clear()
        await msg.answer("Карточка лида потеряна. Откройте Sales Desk ещё раз.")
        return

    if text.lower() in {"отмена", "cancel", "/cancel"}:
        await state.clear()
        await msg.answer("Заметка отменена.", reply_markup=_note_done_kb(lead_id))
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
        await msg.answer("Лид уже назначен другому менеджеру.", reply_markup=_note_done_kb(lead_id))
        return
    except ValueError as exc:
        await msg.answer(str(exc))
        return
    except SalesDeskUnavailable:
        log.exception("Sales Desk note schema unavailable")
        await msg.answer("Sales Desk ещё не готов на сервере. Обновите карточку после миграции.")
        return
    except RuntimeError:
        log.exception("Sales Desk note failed")
        await msg.answer("Не удалось сохранить заметку. Попробуйте открыть карточку ещё раз.")
        return
    except OSError:
        log.exception("Sales Desk note failed")
        await msg.answer("Не удалось сохранить заметку. Попробуйте открыть карточку ещё раз.")
        return
    except sqlite3.Error:
        log.exception("Sales Desk note failed")
        await msg.answer("Не удалось сохранить заметку. Попробуйте открыть карточку ещё раз.")
        return

    await state.clear()
    await msg.answer("✅ Заметка сохранена.", reply_markup=_note_done_kb(lead_id))
