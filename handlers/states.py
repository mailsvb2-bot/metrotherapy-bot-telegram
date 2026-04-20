from aiogram.fsm.state import StatesGroup, State


class InputState(StatesGroup):
    email = State()
    work_time = State()
    home_time = State()
    demo_time = State()
