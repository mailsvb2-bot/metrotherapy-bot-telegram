from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

from services.sales_desk_core import normalize_stage
from services.sales_desk_db import SalesDeskUnavailable
from services.sales_desk_repository import (
    add_note,
    claim_lead,
    get_lead,
    iso_now,
    read_sales_snapshot,
    set_lead_stage,
    set_next_contact,
)
from services.sales_desk_sync import sync_sales_leads as _sync_sales_leads

log = logging.getLogger(__name__)

_SYNC_INTERVAL_SECONDS = 30.0
_SYNC_LOCK = threading.Lock()
_LAST_SYNC_MONOTONIC = 0.0

_STAGE_TITLES = {
    "new": "Новый",
    "contacted": "Связались",
    "qualified": "Заинтересован",
    "checkout": "Оплата начата",
    "won": "Оплатил",
    "lost": "Отказ",
}


def sync_sales_leads(*, limit: int = 5000) -> dict[str, int]:
    return _sync_sales_leads(limit=limit, now_iso=iso_now())


def _sync_if_due() -> None:
    global _LAST_SYNC_MONOTONIC

    now = time.monotonic()
    if now - _LAST_SYNC_MONOTONIC < _SYNC_INTERVAL_SECONDS:
        return
    if not _SYNC_LOCK.acquire(blocking=False):
        return
    try:
        now = time.monotonic()
        if now - _LAST_SYNC_MONOTONIC < _SYNC_INTERVAL_SECONDS:
            return
        sync_sales_leads()
        _LAST_SYNC_MONOTONIC = time.monotonic()
    finally:
        _SYNC_LOCK.release()


def sales_desk_snapshot(
    *,
    filter_name: str = "open",
    admin_id: int | None = None,
    limit: int = 12,
    sync: bool = True,
) -> dict[str, Any]:
    if sync:
        try:
            _sync_if_due()
        except SalesDeskUnavailable:
            raise
        except sqlite3.Error:
            log.warning("Sales Desk source sync database failure", exc_info=True)
        except OSError:
            log.warning("Sales Desk source sync operating failure", exc_info=True)
        except RuntimeError:
            log.warning("Sales Desk source sync runtime failure", exc_info=True)
        except TypeError:
            log.warning("Sales Desk source sync type failure", exc_info=True)
        except ValueError:
            log.warning("Sales Desk source sync value failure", exc_info=True)

    return read_sales_snapshot(
        filter_name=filter_name,
        admin_id=admin_id,
        limit=limit,
        now_iso=iso_now(),
    )


def stage_title(stage: str | None) -> str:
    return _STAGE_TITLES[normalize_stage(stage)]


def format_money(amount_minor: int, currency: str = "RUB") -> str:
    amount = max(0, int(amount_minor or 0))
    normalized_currency = str(currency or "RUB").upper()
    if normalized_currency == "RUB":
        return f"{amount / 100:.2f} ₽"
    return f"{amount / 100:.2f} {normalized_currency}".strip()


def format_sales_overview(snapshot: dict[str, Any]) -> str:
    counts = dict(snapshot.get("counts") or {})
    return "\n".join(
        [
            "🧑‍💼 Sales Desk",
            "",
            "Операционная очередь отдела продаж. Пользовательская воронка и платежи не изменяются.",
            "",
            f"Новые: {int(counts.get('new') or 0)}",
            f"Связались: {int(counts.get('contacted') or 0)}",
            f"Заинтересованы: {int(counts.get('qualified') or 0)}",
            f"Начали оплату: {int(counts.get('checkout') or 0)}",
            f"Оплатили: {int(counts.get('won') or 0)}",
            f"Отказы: {int(counts.get('lost') or 0)}",
            "",
            f"Без ответственного: {int(snapshot.get('unassigned') or 0)}",
            f"Просрочен следующий контакт: {int(snapshot.get('overdue') or 0)}",
            "Выручка выигранных лидов: "
            f"{format_money(int(snapshot.get('won_revenue_minor') or 0))}",
        ]
    )


def format_lead_card(lead: dict[str, Any]) -> str:
    owner = lead.get("assigned_to")
    lines = [
        f"🧑‍💼 Лид #{int(lead.get('id') or 0)}",
        "",
        f"Клиент: {str(lead.get('display_name') or 'Пользователь')}",
        f"User ID: {lead.get('user_id') or '—'}",
        f"Этап: {stage_title(str(lead.get('stage') or 'new'))}",
        f"Ответственный: {owner if owner is not None else 'не назначен'}",
        f"Источник: {str(lead.get('source') or 'organic')}",
        f"Кампания: {str(lead.get('campaign') or '—')}",
        f"Креатив: {str(lead.get('creative') or '—')}",
        "Выручка: "
        f"{format_money(int(lead.get('revenue_minor') or 0), str(lead.get('currency') or 'RUB'))}",
        f"Последняя активность: {str(lead.get('last_activity_at') or '—')}",
        f"Следующий контакт: {str(lead.get('next_contact_at') or 'не назначен')}",
    ]
    notes = list(lead.get("notes") or [])
    if notes:
        lines.extend(["", "Последние заметки:"])
        for note in notes[:3]:
            lines.append(
                f"• {str(note.get('note_text') or '')[:180]} "
                f"— admin {note.get('author_id')}"
            )
    return "\n".join(lines)


def format_lead_history(lead: dict[str, Any]) -> str:
    lines = [f"🧾 История лида #{int(lead.get('id') or 0)}", ""]
    audit = list(lead.get("audit") or [])
    if not audit:
        lines.append("Событий пока нет.")
        return "\n".join(lines)
    for event in audit[:10]:
        lines.append(
            f"• {str(event.get('created_at') or '—')} · "
            f"{str(event.get('event_type') or 'event')} · "
            f"actor {event.get('actor_id')}"
        )
    return "\n".join(lines)


__all__ = [
    "SalesDeskUnavailable",
    "add_note",
    "claim_lead",
    "format_lead_card",
    "format_lead_history",
    "format_money",
    "format_sales_overview",
    "get_lead",
    "sales_desk_snapshot",
    "set_lead_stage",
    "set_next_contact",
    "stage_title",
    "sync_sales_leads",
]
