from aiogram.fsm.state import StatesGroup, State


class InputState(StatesGroup):
    demo_time = State()


class AdminInputState(StatesGroup):
    user_card = State()


class MarketingCopyState(StatesGroup):
    key = State()
    context = State()
    goal = State()


class RolesInputState(StatesGroup):
    grant = State()
    revoke = State()
