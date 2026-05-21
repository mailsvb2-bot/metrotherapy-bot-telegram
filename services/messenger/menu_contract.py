from __future__ import annotations

"""Canonical cross-messenger menu contract.

One source of truth for the main user-facing actions that must exist across
Telegram inline callbacks, VK persistent keyboards and MAX text/menu fallback.

Telegram is the UX source of truth: titles and callback semantics here mirror
``keyboards.inline.kb_main``. Platform adapters may render the actions
differently, but they must not invent a second command vocabulary.
"""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class MenuAction:
    command: str
    title: str
    telegram_callback: str
    vk_color: str = "secondary"
    aliases: tuple[str, ...] = ()


MAIN_MENU_ACTIONS: tuple[MenuAction, ...] = (
    MenuAction(
        command="demo",
        title="🌿 Попробовать бесплатно",
        telegram_callback="demo",
        vk_color="positive",
        aliases=("демо", "попробовать бесплатно", "бесплатная практика"),
    ),
    MenuAction(
        command="full",
        title="🔐 Полный маршрут",
        telegram_callback="full",
        vk_color="primary",
        aliases=("полный маршрут", "полный доступ"),
    ),
    MenuAction(
        command="pay",
        title="💳 Тарифы",
        telegram_callback="sub:menu",
        vk_color="primary",
        aliases=("тарифы", "пакеты практик", "практики", "оплатить", "оплата", "pay"),
    ),
    MenuAction(
        command="gift",
        title="🎁 Подарить",
        telegram_callback="gift:menu",
        aliases=("подарить", "подарок", "gift"),
    ),
    MenuAction(
        command="progress",
        title="📈 Мой прогресс",
        telegram_callback="settings:state",
        vk_color="primary",
        aliases=("мой прогресс", "прогресс", "анализ", "анализ моего состояния"),
    ),
    MenuAction(
        command="settings",
        title="🧠 Настройки",
        telegram_callback="settings:menu",
        aliases=("настройки", "настройки канала", "settings"),
    ),
    MenuAction(
        command="share",
        title="📣 Посоветовать",
        telegram_callback="share:menu",
        aliases=("посоветовать", "поделиться", "share", "пригласить"),
    ),
    MenuAction(
        command="weather",
        title="🌤 Погода",
        telegram_callback="weather:show",
        aliases=("погода", "weather"),
    ),
)

CONTEXT_ACTIONS: tuple[MenuAction, ...] = (
    MenuAction(
        command="continue",
        title="🎧 Получить аудио",
        telegram_callback="demo",
        vk_color="primary",
        aliases=("получить аудио", "продолжить", "continue", "next", "audio"),
    ),
    MenuAction(
        command="done",
        title="✅ Прослушал",
        telegram_callback="mood:done",
        vk_color="positive",
        aliases=("прослушал", "готово", "дослушал", "done"),
    ),
)

TELEGRAM_MAIN_LAYOUT: tuple[tuple[str, ...], ...] = (
    ("🌿 Попробовать бесплатно", "🔐 Полный маршрут"),
    ("💳 Тарифы", "🎁 Подарить"),
    ("📈 Мой прогресс", "🧠 Настройки"),
    ("📣 Посоветовать", "🌤 Погода"),
)


def iter_main_menu_actions() -> Iterable[MenuAction]:
    return iter(MAIN_MENU_ACTIONS)


def main_menu_commands() -> tuple[str, ...]:
    return tuple(action.command for action in MAIN_MENU_ACTIONS)


def main_menu_titles() -> tuple[str, ...]:
    return tuple(action.title for action in MAIN_MENU_ACTIONS)


def telegram_main_callbacks() -> tuple[str, ...]:
    return tuple(action.telegram_callback for action in MAIN_MENU_ACTIONS)


def normalize_menu_command(text: str) -> str | None:
    compact = (text or "").strip().casefold().replace("ё", "е")
    compact = " ".join(compact.split())
    if not compact:
        return None
    if compact in {"⬅️ назад", "назад", "⬅️ меню", "меню", "главное меню", "start", "/start"}:
        return "start"
    for action in MAIN_MENU_ACTIONS + CONTEXT_ACTIONS:
        candidates = {action.command, action.title.casefold().replace("ё", "е"), *action.aliases}
        if compact in {" ".join(str(item).casefold().replace("ё", "е").split()) for item in candidates}:
            return action.command
    return None


def max_numbered_menu_text() -> str:
    """Fallback menu for MAX when native buttons are unavailable.

    MAX native buttons are rendered where supported. The numbered menu keeps
    the exact same Telegram-derived command vocabulary visible as a fallback.
    """
    lines = ["Главное меню", ""]
    for idx, action in enumerate(MAIN_MENU_ACTIONS, start=1):
        lines.append(f"{idx}. {action.title} — отправьте: {action.command}")
    lines.append("")
    lines.append("Дополнительно: continue — получить аудио, done — отметить прослушивание.")
    return "\n".join(lines)