from __future__ import annotations

"""Compatibility import surface for FSM states.

Canonical state definitions live in handlers.text_input_parts.states.
Do not define a second InputState here: aiogram State objects are identity-based,
and duplicate classes create a subtle split-brain where one handler sets a state
and another handler listens to a different state object.
"""

from handlers.text_input_parts.states import (
    AdminInputState,
    InputState,
    MarketingCopyState,
    RolesInputState,
)

__all__ = [
    "InputState",
    "AdminInputState",
    "MarketingCopyState",
    "RolesInputState",
]
