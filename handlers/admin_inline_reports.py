from __future__ import annotations

import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx

from handlers.admin_reports import (
    ab,
    ad_links,
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
    money_clients,
    system_checks,
)

_HANDLERS = {
    "admin:ab": ab.run,
    "admin:adlinks": ad_links.run,
    "admin:demo:brief": demo_brief.run,
    "admin:demo:full": demo_full.run,
    "admin:funnel": funnel.run,
    # The visible button is "Оплаты". It must open the real payment/client list,
    # not a generic conversion counter report.
    "admin:conversion": money_clients.run,
    "admin:payment:problems": payment_problems.run,
    "admin:funnel2": funnel2.run,
    "admin:giftshare": giftshare.run,
    "admin:segments": segments.run,
    "admin:behavior": behavior.run,
    "admin:retention": retention.run,
    "admin:state:last": state_last.run,
    "admin:messenger:overview": messenger_overview.run,
    "admin:system:checks": system_checks.run,
    "admin:money:today": money_clients.run,
    "admin:money:week": money_clients.run,
    "admin:money:month": money_clients.run,
    "admin:money:all": money_clients.run,
}


async def handle(cb: CallbackQuery, state: FSMContext, data: str, ctx: AdminCtx) -> bool:
    log = logging.getLogger(__name__)
    if data.startswith("admin:money:payment:"):
        return await money_clients.run(cb, state, ctx, log)
    if data.startswith("admin:adlinks:create:"):
        return await ad_links.run(cb, state, ctx, log)
    fn = _HANDLERS.get(data)
    if not fn:
        return False
    return await fn(cb, state, ctx, log)
