from __future__ import annotations

from functools import lru_cache

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню (для всех пользователей)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Демо") , KeyboardButton(text="Посоветовать"), KeyboardButton(text="Погода")],
            [KeyboardButton(text="Настройки")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие…",
    )
