from __future__ import annotations

import json
import logging
from datetime import timedelta

from core.time_utils import utc_now
from services.ai.client import OpenAIClient
from services.db import db

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def _demand_snapshot(days: int = 7) -> dict:
    """Sales-demand snapshot for admin price advice.

    Reads paid subscriptions for the last N days. Does not mutate prices.
    """
    since = (utc_now().replace(microsecond=0) - timedelta(days=int(days))).isoformat()
    with db() as conn:
        rows = conn.execute(
            "SELECT scope, COUNT(1) AS n FROM subscriptions "
            "WHERE COALESCE(paid_at, created_at) IS NOT NULL AND COALESCE(paid_at, created_at) >= ? "
            "GROUP BY scope",
            (since,),
        ).fetchall()
    out = {"since_utc": since, "by_scope": {}}
    for r in rows:
        out["by_scope"][str(r["scope"])] = int(r["n"] or 0)
    return out


def recommend_prices() -> dict:
    """AI recommendations for admin price review.

    AI is only an admin/marketing adviser here. It never applies prices
    automatically and must not be represented as a therapist or product coach.
    """
    snapshot = _demand_snapshot(7)

    client = OpenAIClient.from_settings()
    if not client:
        return {"ok": False, "reason": "ai_disabled_or_no_api_key", "snapshot": snapshot}

    prompt = (
        "Ты AI-помощник маркетолога и администратора продукта. "
        "Дай осторожные рекомендации по ценам тарифов Telegram-бота на основе спроса. "
        "Нужно предложить коэффициент (multiplier) для каждого scope (morning/evening/both) в диапазоне 0.8..1.3. "
        "Если спрос выше — можно чуть повышать, если ниже — можно чуть снижать. "
        "Не обещай терапевтические результаты и не делай медицинских выводов. "
        "Верни строго JSON вида: {\"morning\":1.0,\"evening\":1.0,\"both\":1.0,\"comment\":\"...\"}.\n\n"
        f"Данные спроса (JSON): {json.dumps(snapshot, ensure_ascii=False)}"
    )

    txt = client.chat(
        messages=[
            {"role": "system", "content": "Отвечай строго JSON, без текста вокруг."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=200,
    )

    if not txt:
        return {"ok": False, "reason": "api_error", "snapshot": snapshot}

    try:
        obj = json.loads(txt)
        out = {}
        for k in ("morning", "evening", "both"):
            v = float(obj.get(k, 1.0))
            if v < 0.8:
                v = 0.8
            if v > 1.3:
                v = 1.3
            out[k] = round(v, 2)
        out["comment"] = str(obj.get("comment", "")).strip()
        return {"ok": True, "snapshot": snapshot, "recommendation": out}
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("ai_pricing_bad_response", extra={"error_type": type(exc).__name__})
        return {"ok": False, "reason": "bad_json", "snapshot": snapshot}


def record_price_recommendation(payload: dict):
    with db() as conn:
        conn.execute(
            "INSERT INTO ai_decisions(user_id, kind, value, meta, created_at_utc) VALUES(?,?,?,?,?)",
            (0, "price_reco", "ok", json.dumps(payload or {}, ensure_ascii=False), _utc_now_iso()),
        )
