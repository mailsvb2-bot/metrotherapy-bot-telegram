from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
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
    """Минимальная сводка пользователя для AI-решения.

    Принцип B/D: всё из БД, без скрытых зависимостей.
    """
    user_id = int(user_id)
    with db() as conn:
        # последняя активность: час в локальном TZ
        hour = None
        try:
            hour = recent_hour_local(user_id)
        except (sqlite3.Error, ValueError, TypeError):
            _warn_db_rate_limited("recent_hour_local failed", user_id=user_id)
            hour = None

        # открыл тарифы?
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

        # сколько демо ack
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


def choose_funnel_profile(user_id: int, *, kind: str = "work") -> FunnelProfile:
    """Выбрать профиль воронки.

    UX не меняем: это влияет только на частоту/жёсткость касаний.
    """
    s = _user_summary(int(user_id))

    # Подписка активна => никаких продаж
    if s.get("sub_active"):
        return "soft"

    # Детерминированный fallback без AI:
    # - если человек уже открыл тарифы => мягкий режим
    # - если демо прослушал, но тарифы не открывал => стандарт
    # - если демо прослушал несколько раз и игнорирует => urgent
    fallback: FunnelProfile
    if s.get("opened_tariffs"):
        fallback = "soft"
    elif int(s.get("demo_acks") or 0) >= 2:
        fallback = "urgent"
    else:
        fallback = "standard"

    client = OpenAIClient.from_settings()
    if not client:
        return fallback

    prompt = (
        "Ты помощник продукта Telegram-бота. "
        "Твоя задача: выбрать профиль коммуникаций после демо: soft / standard / urgent. "
        "soft — минимум сообщений, standard — обычный режим, urgent — чуть более настойчиво, но без спама. "
        "Отвечай строго одним словом: soft или standard или urgent.\n\n"
        f"Данные пользователя (JSON): {json.dumps(s, ensure_ascii=False)}\n"
        f"Kind демо: {kind}\n"
    )

    txt = client.chat(
        messages=[
            {"role": "system", "content": "Ты выбираешь только один из вариантов: soft, standard, urgent."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=10,
    )

    val = (txt or "").strip().lower()
    if val in ("soft", "standard", "urgent"):
        return val  # type: ignore[return-value]
    return fallback


def record_funnel_profile(user_id: int, profile: str, *, meta: dict | None = None):
    """Записать решение AI в БД для доказуемости (Принцип H)."""
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
