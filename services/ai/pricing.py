from __future__ import annotations

import json
import logging
from datetime import timedelta
from json import JSONDecoder
from typing import Any

from core.time_utils import utc_now
from services.ai.client import OpenAIClient
from services.ai.policy import admin_marketing_system_prompt
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


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from model output.

    Providers sometimes wrap valid JSON in Markdown fences or add a short
    sentence around it. The admin price surface needs a strict object, not a
    free-form answer, so we only accept a real JSON object and ignore wrappers.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("empty_ai_price_response")

    decoder = JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        obj, _end = decoder.raw_decode(cleaned[index:])
        if isinstance(obj, dict):
            return obj
    raise ValueError("ai_price_json_object_not_found")


def _coerce_multiplier(value: object) -> float:
    v = float(value)
    if v < 0.8:
        return 0.8
    if v > 1.3:
        return 1.3
    return round(v, 2)


def _fallback_recommendation(snapshot: dict) -> dict[str, Any]:
    """Deterministic safe fallback when the model output is unusable."""
    by_scope = snapshot.get("by_scope") or {}
    total = sum(int(by_scope.get(scope, 0) or 0) for scope in ("morning", "evening", "both"))
    comment = (
        "ИИ вернул ответ не в нужном формате, поэтому показана безопасная базовая подсказка: "
        "цены не менять до накопления более устойчивой статистики оплат."
        if total < 10
        else "ИИ вернул ответ не в нужном формате, поэтому показана безопасная базовая подсказка: менять цены вручную только после проверки спроса по каждому тарифу."
    )
    return {"morning": 1.0, "evening": 1.0, "both": 1.0, "comment": comment}


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
        "Дай осторожные рекомендации по ценам тарифов Telegram-бота на основе спроса. "
        "Нужно предложить коэффициент (multiplier) для каждого scope (morning/evening/both) в диапазоне 0.8..1.3. "
        "Если спрос выше — можно чуть повышать, если ниже — можно чуть снижать. "
        "Верни строго JSON без Markdown и без пояснений: "
        "{\"morning\":1.0,\"evening\":1.0,\"both\":1.0,\"comment\":\"...\"}.\n\n"
        f"Данные спроса (JSON): {json.dumps(snapshot, ensure_ascii=False)}"
    )

    txt = client.chat(
        messages=[
            {
                "role": "system",
                "content": admin_marketing_system_prompt(task="дать JSON-рекомендацию цен без автоматического применения"),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=200,
    )

    if not txt:
        return {"ok": False, "reason": "api_error", "snapshot": snapshot}

    try:
        obj = _extract_json_object(txt)
        out = {k: _coerce_multiplier(obj.get(k, 1.0)) for k in ("morning", "evening", "both")}
        out["comment"] = str(obj.get("comment", "")).strip()
        return {"ok": True, "snapshot": snapshot, "recommendation": out}
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        log.warning("ai_pricing_bad_response", extra={"error_type": type(exc).__name__})
        return {
            "ok": True,
            "snapshot": snapshot,
            "recommendation": _fallback_recommendation(snapshot),
            "warning": "bad_json_fallback",
        }


def record_price_recommendation(payload: dict):
    with db() as conn:
        conn.execute(
            "INSERT INTO ai_decisions(user_id, kind, value, meta, created_at_utc) VALUES(?,?,?,?,?)",
            (0, "price_reco", "ok", json.dumps(payload or {}, ensure_ascii=False), _utc_now_iso()),
        )
