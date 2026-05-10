from __future__ import annotations

import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx

from handlers.admin_reports import (
    ab,
    demo_brief,
    demo_full,
    funnel,
    conversion,
    funnel2,
    giftshare,
    segments,
    behavior,
    retention,
    state_last,
    messenger_overview,
    payment_problems,
)

_HANDLERS = {
    "admin:ab": ab.run,
    "admin:demo:brief": demo_brief.run,
    "admin:demo:full": demo_full.run,
    "admin:funnel": funnel.run,
    "admin:conversion": conversion.run,
    "admin:payment:problems": payment_problems.run,
    "admin:funnel2": funnel2.run,
    "admin:giftshare": giftshare.run,
    "admin:segments": segments.run,
    "admin:behavior": behavior.run,
    "admin:retention": retention.run,
    "admin:state:last": state_last.run,
    "admin:messenger:overview": messenger_overview.run,
}


async def handle(cb: CallbackQuery, state: FSMContext, data: str, ctx: AdminCtx) -> bool:
    log = logging.getLogger(__name__)
    fn = _HANDLERS.get(data)
    if not fn:
        return False
    return await fn(cb, state, ctx, log)
