from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from services.payments.reconciliation import payment_problem_summary


def _rub(amount_minor: int | None, currency: str | None) -> str:
    try:
        amount = int(amount_minor or 0) / 100
    except (TypeError, ValueError):
        amount = 0
    return f"{amount:.2f} {currency or 'RUB'}"


def _format(rows: list[dict]) -> str:
    if not rows:
        return (
            "✅ Проблем с оплатами сейчас не видно.\n\n"
            "Я проверяю платежи, которые пришли от платёжного провайдера, но требуют внимания: "
            "отмена, ожидание подтверждения, отсутствие пользователя или другая пометка."
        )

    lines = [
        "⚠️ Оплаты, которые нужно проверить\n",
        "Это не меняет доступ автоматически. Это список для ручной проверки и поддержки.",
        "",
    ]
    for row in rows:
        lines.append(
            "\n".join(
                [
                    f"• Платёж #{row.get('id')}",
                    f"  Пользователь: {row.get('user_id') or 'не найден'}",
                    f"  Провайдер: {row.get('provider_charge_id') or 'нет id'}",
                    f"  Сумма: {_rub(row.get('amount'), row.get('currency'))}",
                    f"  Статус: {row.get('provider_status') or 'не указан'}",
                    f"  Что проверить: {row.get('problem') or 'статус платежа'}",
                    f"  Когда: {row.get('reconciled_at') or row.get('created_at') or '-'}",
                ]
            )
        )
    return "\n\n".join(lines)


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    rows = await asyncio.to_thread(payment_problem_summary, 20)
    await safe_edit(cb, _format(rows), reply_markup=ctx.staff_kb)
    return True
