from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.admin_inline_common import AdminCtx
from services.admin_permissions import (
    SALES_DESK_PERMISSION,
    SALES_MESSAGE_PERMISSION,
    SALES_WRITE_PERMISSION,
)
from services.sales_desk_core import SALES_STAGES, can_transition, normalize_filter

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


def can_read(ctx: AdminCtx) -> bool:
    if ctx.is_superadmin:
        return True
    if ctx.allowed_perms is None:
        return True
    return SALES_DESK_PERMISSION in ctx.allowed_perms


def can_write(ctx: AdminCtx) -> bool:
    if ctx.is_superadmin:
        return True
    return (
        ctx.allowed_perms is not None
        and SALES_WRITE_PERMISSION in ctx.allowed_perms
    )


def can_message(ctx: AdminCtx) -> bool:
    if ctx.is_superadmin:
        return True
    return (
        can_write(ctx)
        and ctx.allowed_perms is not None
        and SALES_MESSAGE_PERMISSION in ctx.allowed_perms
    )


def callback_parts(data: str | None) -> list[str]:
    return [part for part in str(data or "").split(":") if part]


def lead_id_from_callback(data: str | None) -> int:
    parts = callback_parts(data)
    if not parts:
        raise ValueError("sales_lead_id_missing")
    try:
        return int(parts[-1])
    except (TypeError, ValueError) as exc:
        raise ValueError("sales_lead_id_missing") from exc


def filter_from_callback(data: str | None) -> str:
    parts = callback_parts(data)
    if "list" not in parts:
        return "open"
    index = parts.index("list") + 1
    return normalize_filter(parts[index] if index < len(parts) else "open")


def stage_from_callback(data: str | None) -> str:
    parts = callback_parts(data)
    if "stage" not in parts:
        raise ValueError("sales_stage_missing")
    index = parts.index("stage") + 1
    if index >= len(parts) or parts[index] not in SALES_STAGES:
        raise ValueError("invalid_sales_stage")
    return parts[index]


def follow_days_from_callback(data: str | None) -> int | None:
    parts = callback_parts(data)
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


def home_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin:menu")]


def overview_keyboard(snapshot: dict[str, Any]) -> InlineKeyboardMarkup:
    selected = normalize_filter(str(snapshot.get("filter") or "open"))
    keyboard_rows: list[list[InlineKeyboardButton]] = []
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
        keyboard_rows.append(row)

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
        owner_text = (
            f" · {owner}"
            if owner is not None
            else " · без ответственного"
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{marker}#{lead_id} "
                        f"{str(lead.get('display_name') or 'Лид')[:28]}"
                        f"{owner_text}"
                    ),
                    callback_data=f"admin:sales:lead:{lead_id}",
                )
            ]
        )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=f"admin:sales:list:{selected}",
            )
        ]
    )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🤖 Growth Autopilot",
                callback_data="admin:growth:autopilot",
            )
        ]
    )
    keyboard_rows.append(home_row())
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def card_keyboard(
    lead: dict[str, Any],
    *,
    write_allowed: bool,
    message_allowed: bool,
) -> InlineKeyboardMarkup:
    lead_id = int(lead.get("id") or 0)
    current = str(lead.get("stage") or "new")
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    if write_allowed:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text="🙋 Взять лид",
                    callback_data=f"admin:sales:claim:{lead_id}",
                ),
                InlineKeyboardButton(
                    text="📝 Заметка",
                    callback_data=f"admin:sales:note:{lead_id}",
                ),
            ]
        )
        if message_allowed:
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text="✉️ Написать лиду",
                        callback_data=f"admin:sales:message:{lead_id}",
                    )
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
            keyboard_rows.append(stage_buttons[index:index + 2])
        if current not in {"won", "lost"}:
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text="⏰ +1 день",
                        callback_data=f"admin:sales:follow:1:{lead_id}",
                    ),
                    InlineKeyboardButton(
                        text="⏰ +3 дня",
                        callback_data=f"admin:sales:follow:3:{lead_id}",
                    ),
                ]
            )
            follow_row = [
                InlineKeyboardButton(
                    text="⏰ +7 дней",
                    callback_data=f"admin:sales:follow:7:{lead_id}",
                )
            ]
            if lead.get("next_contact_at"):
                follow_row.append(
                    InlineKeyboardButton(
                        text="Убрать follow-up",
                        callback_data=f"admin:sales:follow:clear:{lead_id}",
                    )
                )
            keyboard_rows.append(follow_row)
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🧾 История",
                callback_data=f"admin:sales:history:{lead_id}",
            )
        ]
    )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К лидам",
                callback_data="admin:sales:list:open",
            )
        ]
    )
    keyboard_rows.append(home_row())
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def history_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ К карточке",
                    callback_data=f"admin:sales:lead:{int(lead_id)}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К лидам",
                    callback_data="admin:sales:list:open",
                )
            ],
            home_row(),
        ]
    )


def input_done_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть карточку",
                    callback_data=f"admin:sales:lead:{int(lead_id)}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К лидам",
                    callback_data="admin:sales:list:open",
                )
            ],
            home_row(),
        ]
    )


def cancel_input_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"admin:sales:lead:{int(lead_id)}",
                )
            ]
        ]
    )
