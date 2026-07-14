from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AdminManageState(StatesGroup):
    waiting_tariffs_text = State()
    # Ввод одной цены после выбора тарифа кнопкой (ожидаем целое число в рублях)
    waiting_tariff_single_price = State()
    waiting_admin_user = State()
    waiting_sales_note = State()
    waiting_sales_message = State()
