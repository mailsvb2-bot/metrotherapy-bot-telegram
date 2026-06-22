from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from services.disaster_recovery_status import format_disaster_recovery_status_for_admin
from services.release_control_report import format_release_control_report
from services.storage_legacy_audit import format_storage_legacy_audit_for_admin


def _format_report() -> str:
    parts = [
        format_release_control_report(limit=25),
        format_storage_legacy_audit_for_admin(),
        format_disaster_recovery_status_for_admin(),
    ]
    return "\n\n".join(parts)


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    text = await asyncio.to_thread(_format_report)
    await safe_edit(cb, text, reply_markup=ctx.staff_kb)
    return True
