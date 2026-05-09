from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from core.time_utils import utc_now
from typing import Literal

from services.db import db
from services.subscription import is_active as is_sub_active
from services.state_log import recent_hour_local
from services.ai.client import OpenAIClient

log = logging.getLogger(__name__)
_last_db_warn_ts: float = 0.0


def _warn_db_rate_limited(msg: str, *, user_id: int) -> None:
    global _last_db_warn_ts
    now = time.time()
    if now - _last_db_warn_ts < 300.0:
        return
    _last_db_warn_ts = now
    log.warning(msg, extra={"user_id": int(user_id)}, exc_info=True)


FunnelProfile = Literal["soft", "standard", "urgent"]


def _utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def _user_summary(user_id: int) -> dict:
    """Minimal DB-based user summary for admin/marketing funnel advice."""
    user_id = int(user_id)
    with db() as conn:
        hour = None
        try:
            hour = recent_hour_local(user_id)
        except (sqlite3.Error, ValueError, TypeError):
            _warn_db_rate_limited("recent_hour_local failed", user_id=user_id)
            hour = None

        opened_tariffs = False
        try:
            r = conn.execute(
                "SELECT 1 FROM events WHERE user_id=? AND event='sub_menu_open' ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            opened_tariffs = bool(r)
        except sqlite3.Error:
            _warn_db_rate_limited("DB error reading opened tariffs", user_id=user_id)
            opened_tariffs = False

        demo_acks = 0
        try:
            r = conn.execute(
                "SELECT COUNT(1) AS n FROM demo_events WHERE user_id=? AND ack_at_utc IS NOT NULL",
                (user_id,),
            ).fetchone()
            demo_acks = int(r["n"] or 0) if r else 0
        except sqlite3.Error:
            _warn_db_rate_limited("DB error reading demo_acks", user_id=user_id)
            demo_acks = 0
        except (KeyError, TypeError, ValueError):
            log.warning("Bad demo_acks row format", extra={"user_id": int(user_id)}, exc_info=True)
            demo_acks = 0

    return {
        "user_id": user_id,
        "sub_active": bool(is_sub_active(user_id)),
        "opened_tariffs": bool(opened_tariffs),
        "demo_acks": int(demo_acks),
        "last_hour_local": hour,
    }


def _fallback_funnel_profile(summary: dict) -> FunnelProfile:
    if summary.get("sub_active"):
        return "soft"
    if summary.get("opened_tariffs"):
        return "soft"
    if int(summary.get("demo_acks") or 0) >= 2:
        return "urgent"
    return "standard"


def _normalize_profile(value: str | None, fallback: FunnelProfile) -> FunnelProfile:
    val = (value or "").strip().lower()
    if val in ("soft", "standard", "urgent"):
        return val  # type: ignore[return-value]
    return fallback


def choose_funnel_profile(user_id: int, *, kind: str = "work") -> FunnelProfile:
    """Choose post-demo marketing funnel profile: soft / standard / urgent.

    AI is used only as an admin/marketing adviser. It does not act as a therapist,
    does not generate user-facing therapeutic claims, and never changes UX directly.
    """
    summary = _user_summary(int(user_id))
    fallback = _fallback_funnel_profile(summary)

    client = OpenAIClient.from_settings()
    if not client:
        return fallback

    prompt = (
        "Ты AI-помощник маркетолога и администратора Telegram-бота. "
        "Твоя задача: выбрать профиль маркетинговой коммуникации после демо: soft / standard / urgent. "
        "soft — минимум сообщений, standard — обычный режим, urgent — чуть более настойчиво, но без спама. "
        "Не выступай терапевтом, врачом или психологом; не давай терапевтических выводов. "
        "Отвечай строго одним словом: soft или standard или urgent.\n\n"
        f"Данные пользователя (JSON): {json.dumps(summary, ensure_ascii=False)}\n"
        f"Kind демо: {kind}\n"
    )

    txt = client.chat(
        messages=[
            {"role": "system", "content": "Выбери только один маркетинговый профиль: soft, standard, urgent."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=10,
    )
    return _normalize_profile(txt, fallback)


async def choose_funnel_profile_async(user_id: int, *, kind: str = "work") -> FunnelProfile:
    """Async-safe wrapper for Telegram handlers."""
    return await asyncio.to_thread(choose_funnel_profile, int(user_id), kind=kind)


def record_funnel_profile(user_id: int, profile: str, *, meta: dict | None = None):
    """Record AI/fallback marketing profile decision for auditability."""
    with db() as conn:
        conn.execute(
            "INSERT INTO ai_decisions(user_id, kind, value, meta, created_at_utc) VALUES(?,?,?,?,?)",
            (
                int(user_id),
                "funnel_profile",
                str(profile),
                json.dumps(meta or {}, ensure_ascii=False),
                _utc_now_iso(),
            ),
        )
