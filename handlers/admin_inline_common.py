from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import settings
from services.roles import ROLE_ADMIN, ROLE_SUPPORT, ROLE_MARKETING, user_roles

_ADMIN_SECTIONS: dict[str, str] = {
    "tariffs": "💳 Тарифы",
    "roles": "👥 Роли",
    "perms": "🔐 Доступы",
    "users": "👤 Пользователи",
    "reports": "📊 Отчёты",
    "copy": "✍️ Тексты",
    "states": "🧠 Состояния",
}


def _admin_breadcrumb(data: str | None) -> str | None:
    if not data or not data.startswith("admin:"):
        return None
    if data == "admin:menu":
        return "🛠 Админ-панель"
    parts = data.split(":")
    if len(parts) < 2:
        return "🛠 Админ-панель"
    section_key = parts[1]
    section = _ADMIN_SECTIONS.get(section_key)
    if section:
        return f"🛠 Админ-панель › {section}"
    return "🛠 Админ-панель"


def _ensure_admin_home_button(reply_markup):
    """Make sure every admin screen has a 'home' button back to admin menu."""
    if reply_markup is None:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏠 Админ-меню", callback_data="admin:menu")]]
        )
    if not isinstance(reply_markup, InlineKeyboardMarkup):
        return reply_markup

    for row in reply_markup.inline_keyboard:
        for btn in row:
            if getattr(btn, "callback_data", None) == "admin:menu":
                return reply_markup

    reply_markup.inline_keyboard.append(
        [InlineKeyboardButton(text="🏠 Админ-меню", callback_data="admin:menu")]
    )
    return reply_markup


def _ensure_admin_back_button(reply_markup, *, enabled: bool):
    if not enabled:
        return reply_markup
    if reply_markup is None:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]]
        )
    if not isinstance(reply_markup, InlineKeyboardMarkup):
        return reply_markup

    for row in reply_markup.inline_keyboard:
        for btn in row:
            if getattr(btn, "callback_data", None) == "admin:back":
                return reply_markup

    reply_markup.inline_keyboard.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]
    )
    return reply_markup


# --- Admin UI stack (single message) ---
_ADMIN_NAV_STACK_KEY = "admin_nav_stack"
_ADMIN_NAV_CURRENT_KEY = "admin_nav_current"


def _kb_serialize(kb: InlineKeyboardMarkup | None) -> list[list[dict]] | None:
    if kb is None:
        return None
    if not isinstance(kb, InlineKeyboardMarkup):
        return None
    out: list[list[dict]] = []
    for row in kb.inline_keyboard:
        out_row: list[dict] = []
        for b in row:
            out_row.append(
                {
                    "text": b.text,
                    "callback_data": getattr(b, "callback_data", None),
                    "url": getattr(b, "url", None),
                }
            )
        out.append(out_row)
    return out


def _kb_deserialize(data: list[list[dict]] | None) -> InlineKeyboardMarkup | None:
    if not data:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in data:
        btns: list[InlineKeyboardButton] = []
        for b in row:
            cd = b.get("callback_data")
            url = b.get("url")
            if cd:
                btns.append(InlineKeyboardButton(text=str(b.get("text", "")), callback_data=cd))
            elif url:
                btns.append(InlineKeyboardButton(text=str(b.get("text", "")), url=url))
        if btns:
            rows.append(btns)
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def safe_edit(cb: CallbackQuery, text, reply_markup=None):
    """Legacy safe edit (no back-stack)."""
    bc = _admin_breadcrumb(getattr(cb, "data", None))
    if bc and isinstance(text, str) and not text.startswith("🛠"):
        text = f"{bc}\n\n" + text
    reply_markup = _ensure_admin_home_button(reply_markup)

    if not isinstance(text, str):
        logging.getLogger(__name__).debug("safe_edit got non-str text: %s", type(text))
        try:
            if isinstance(text, (dict, list)):
                text = json.dumps(text, ensure_ascii=False, indent=2, default=str)
            else:
                text = str(text)
        except (TypeError, ValueError):
            text = str(text)

    try:
        await cb.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
    except (TelegramAPIError, asyncio.TimeoutError):
        pass

    try:
        if cb.message:
            await cb.message.answer(text, reply_markup=reply_markup)
    except (TelegramAPIError, asyncio.TimeoutError):
        logging.getLogger(__name__).exception("safe_edit fallback send failed")


async def safe_edit_admin(
    cb: CallbackQuery,
    state,
    text,
    reply_markup=None,
    *,
    push: bool = True,
    reset_stack: bool = False,
):
    """Admin panel renderer with navigation stack.

    Replaces the same message (no clutter) and adds Back/Home automatically.
    """
    if state is None:
        return await safe_edit(cb, text, reply_markup=reply_markup)

    data = await state.get_data()
    stack = list(data.get(_ADMIN_NAV_STACK_KEY) or [])

    if reset_stack:
        stack = []

    if push:
        cur = data.get(_ADMIN_NAV_CURRENT_KEY)
        if cur:
            stack.append(cur)

    bc = _admin_breadcrumb(getattr(cb, "data", None))
    if bc and isinstance(text, str) and not text.startswith("🛠"):
        text = f"{bc}\n\n" + text

    can_back = len(stack) > 0
    kb = reply_markup
    kb = _ensure_admin_back_button(kb, enabled=can_back)
    kb = _ensure_admin_home_button(kb)

    cur_view = {"text": text if isinstance(text, str) else str(text), "kb": _kb_serialize(kb if isinstance(kb, InlineKeyboardMarkup) else None)}
    await state.update_data({_ADMIN_NAV_STACK_KEY: stack, _ADMIN_NAV_CURRENT_KEY: cur_view})

    try:
        await cb.message.edit_text(cur_view["text"], reply_markup=kb)
        return
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
    except (TelegramAPIError, asyncio.TimeoutError):
        pass

    try:
        await cb.message.answer(cur_view["text"], reply_markup=kb)
    except (TelegramAPIError, asyncio.TimeoutError):
        logging.getLogger(__name__).exception("safe_edit_admin fallback send failed")


async def admin_nav_back(cb: CallbackQuery, state) -> bool:
    """Handle admin:back navigation."""
    if (cb.data or "") != "admin:back":
        return False
    if state is None:
        return False

    data = await state.get_data()
    stack = list(data.get(_ADMIN_NAV_STACK_KEY) or [])
    if not stack:
        return False

    prev = stack.pop()
    text = prev.get("text", "")
    kb = _kb_deserialize(prev.get("kb"))

    await state.update_data({_ADMIN_NAV_STACK_KEY: stack, _ADMIN_NAV_CURRENT_KEY: prev})

    kb2 = _ensure_admin_back_button(kb, enabled=len(stack) > 0)
    kb2 = _ensure_admin_home_button(kb2)
    try:
        await cb.message.edit_text(text, reply_markup=kb2)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb2)
    return True


@dataclass(frozen=True)
class AdminCtx:
    uid: int
    roles: set[str]
    staff_kb: object
    is_superadmin: bool
    allowed_perms: set[str] | None


def is_superadmin(uid: int) -> bool:
    return uid in settings.admin_id_list


def get_staff_roles(uid: int) -> set[str]:
    if is_superadmin(uid):
        return {ROLE_ADMIN, ROLE_SUPPORT, ROLE_MARKETING}
    return set(user_roles(uid) or set())


def fmt_sec(x: int | None) -> str:
    if x is None:
        return "-"
    m = int(x) // 60
    s = int(x) % 60
    return f"{m:02d}:{s:02d}"


def fmt_ts(ts_iso: str | None, tz: ZoneInfo) -> str:
    if not ts_iso:
        return "-"
    try:
        return datetime.fromisoformat(ts_iso).astimezone(tz).strftime("%H:%M:%S")
    except ValueError:
        logging.getLogger(__name__).exception("Bad timestamp format")
        return ts_iso
