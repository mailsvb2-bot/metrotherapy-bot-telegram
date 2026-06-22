from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from keyboards.inline import kb_admin_ad_links
from services.admin_ad_links import (
    ad_links_report,
    create_ad_link,
    format_ad_links_report,
    format_created_ad_link,
)

_SOURCES = {"telegram_ads", "telegram_post", "partner"}


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    data = str(cb.data or "")
    if data.startswith("admin:adlinks:create:"):
        source = data.rsplit(":", 1)[-1]
        if source not in _SOURCES:
            await safe_edit(cb, "❌ Не понял источник рекламы.", reply_markup=kb_admin_ad_links())
            return True
        item = await asyncio.to_thread(create_ad_link, source)
        await safe_edit(cb, format_created_ad_link(item), reply_markup=kb_admin_ad_links())
        return True

    report = await asyncio.to_thread(ad_links_report)
    await safe_edit(cb, format_ad_links_report(report), reply_markup=kb_admin_ad_links())
    return True
