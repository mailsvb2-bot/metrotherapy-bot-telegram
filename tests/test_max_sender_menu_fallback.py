from __future__ import annotations

from runtime import messenger_max_ui as max_ui
from services.messenger.menu_contract import MAIN_MENU_ACTIONS


def test_max_sender_adds_numbered_menu_to_main_menu_text():
    prepared = max_ui.prepare_text("Главное меню\n\nВыберите маршрут.")

    for action in MAIN_MENU_ACTIONS:
        assert action.title in prepared
        assert f"отправьте: {action.command}" in prepared
    assert "continue" in prepared
    assert "done" in prepared


def test_max_sender_does_not_duplicate_numbered_menu():
    original = "Главное меню\n\n1. demo — отправьте: demo"

    assert max_ui.prepare_text(original) == original


def test_max_sender_does_not_touch_non_menu_text():
    text = "🔐 Полный маршрут\n\nНажмите continue."

    assert max_ui.prepare_text(text) == text
