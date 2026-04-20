from __future__ import annotations

from aiogram import Router

# Re-export states used by other modules
from handlers.text_input_parts.states import (
    InputState,
    AdminInputState,
    MarketingCopyState,
    RolesInputState,
)

# Root router that includes all sub-routers
router = Router()

from handlers.text_input_parts import demo as _demo
from handlers.text_input_parts import admin_users as _admin_users
from handlers.text_input_parts import marketing_copy as _marketing_copy
from handlers.text_input_parts import admin_roles as _admin_roles

router.include_router(_demo.router)
router.include_router(_admin_users.router)
router.include_router(_marketing_copy.router)
router.include_router(_admin_roles.router)
