from __future__ import annotations

from aiogram import Router

from handlers.flow import analysis as analysis_flow
from handlers.flow import settings_core

# Public router imported by app.py
router = Router()
router.include_router(settings_core.router)
router.include_router(analysis_flow.router)

__all__ = ["router"]
