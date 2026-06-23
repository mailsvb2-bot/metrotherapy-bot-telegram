from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import ADMIN_IDS
from keyboards import inline as kb_inline
from handlers.admin_inline_tariffs import kb_tariffs_nav
from services.roles import user_roles

router = Router()

def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(ADMIN_IDS)

def _fmt_kb(markup) -> str:
    # InlineKeyboardMarkup
    rows = getattr(markup, "inline_keyboard", []) or []
    lines: list[str] = []
    for r_i, row in enumerate(rows, start=1):
        parts = []
        for b in row:
            t = getattr(b, "text", "?")
            cb = getattr(b, "callback_data", None)
            url = getattr(b, "url", None)
            if cb:
                parts.append(f"{t} → {cb}")
            elif url:
                parts.append(f"{t} → {url}")
            else:
                parts.append(t)
        lines.append(f"{r_i}. " + " | ".join(parts))
    return "\n".join(lines) if lines else "(пусто)"


def _maybe_call(obj, names: list[str], *args, **kwargs):
    """Попытаться вызвать функцию/фабрику клавиатуры по одному из имён."""
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn(*args, **kwargs)
    return None

@router.message(Command("kb_debug"))
async def kb_debug_cmd(message: Message) -> None:
    user_id = _message_user_id(message)
    if user_id is None or not _is_admin(user_id):
        return

    roles = set(user_roles(user_id))
    is_superadmin = user_id in set(ADMIN_IDS)

    blocks: list[tuple[str, object]] = [
        ("Главное меню", kb_inline.kb_main(user_id)),
        ("Панель (staff)", kb_inline.kb_staff_menu(roles=roles, is_superadmin=is_superadmin)),
        ("Тарифы (admin)", kb_tariffs_nav()),
        (
            "Погода",
            _maybe_call(kb_inline, ["kb_weather", "kb_weather_menu"]) or kb_inline.kb_back_main(),
        ),
        (
            "Настройки",
            _maybe_call(kb_inline, ["kb_settings", "kb_settings_menu"]) or kb_inline.kb_back_main(),
        ),
    ]

    header = f"KB DEBUG\nuser_id={user_id}\nroles={sorted(list(roles))}\nis_superadmin={is_superadmin}\n"
    await message.answer(header)

    for title, markup in blocks:
        txt = _fmt_kb(markup)
        # Telegram message limit ~4096; chunk if needed
        body = f"*{title}*\n```\n{txt}\n```"

        if len(body) <= 3900:
            await message.answer(body, parse_mode="Markdown")
        else:
            # crude chunking
            chunk = body
            while chunk:
                await message.answer(chunk[:3900], parse_mode="Markdown")
                chunk = chunk[3900:]