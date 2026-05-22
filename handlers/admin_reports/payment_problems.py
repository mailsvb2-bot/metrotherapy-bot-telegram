from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from services.admin_growth_ops import access_alerts, format_access_alerts
from services.payments.reconciliation import payment_problem_summary
from services.premium_entitlements import consultation_requests_summary


def _rub(amount_minor: int | None, currency: str | None) -> str:
    try:
        amount = int(amount_minor or 0) / 100
    except (TypeError, ValueError):
        amount = 0
    return f"{amount:.2f} {currency or 'RUB'}"


def _format_consultations(rows: list[dict]) -> str:
    if not rows:
        return "✅ Новых заявок на консультацию сейчас нет."
    lines = ["👤 Заявки на консультацию", ""]
    for row in rows:
        lines.append(
            "\n".join(
                [
                    f"• Заявка #{row.get('id')}",
                    f"  Пользователь: {row.get('user_id')}",
                    f"  Платформа: {row.get('platform')}",
                    f"  Внешний ID: {row.get('external_user_id') or '-'}",
                    f"  Пакет: {row.get('package_id')}",
                    f"  Платёж: {row.get('provider_payment_id') or '-'}",
                    f"  Статус: {row.get('status')}",
                    f"  Создано: {row.get('created_at') or '-'}",
                    "  Контакт/заметка:",
                    f"  {row.get('contact_payload') or '-'}",
                ]
            )
        )
    return "\n\n".join(lines)


def _format(rows: list[dict], missing_access: list[dict] | None = None, consultations: list[dict] | None = None) -> str:
    missing_access = missing_access or []
    consultations = consultations or []
    access_text = format_access_alerts(missing_access)
    consultation_text = _format_consultations(consultations)
    if not rows:
        return (
            "✅ Проблем с оплатами от платёжного провайдера сейчас не видно.\n\n"
            + access_text
            + "\n\n"
            + consultation_text
            + "\n\nЯ проверяю платежи, которые пришли от платёжного провайдера, но требуют внимания: "
            "отмена, ожидание подтверждения, отсутствие пользователя, успешная оплата без активного доступа или другая пометка."
        )

    lines = [
        "⚠️ Оплаты, которые нужно проверить\n",
        "Это не меняет доступ автоматически. Это список для ручной проверки и поддержки.",
        "",
        access_text,
        "",
        consultation_text,
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
    rows, missing_access, consultations = await asyncio.gather(
        asyncio.to_thread(payment_problem_summary, 20),
        asyncio.to_thread(access_alerts, limit=20),
        asyncio.to_thread(consultation_requests_summary, 20),
    )
    await safe_edit(cb, _format(rows, missing_access, consultations), reply_markup=ctx.staff_kb)
    return True