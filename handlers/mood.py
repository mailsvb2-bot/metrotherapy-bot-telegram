from __future__ import annotations

from aiogram import Router

from handlers.mood_flow import body, charts, done, ratings

router = Router()
router.include_router(ratings.router)
router.include_router(done.router)
router.include_router(charts.router)
router.include_router(body.router)

__all__ = ["router"]
