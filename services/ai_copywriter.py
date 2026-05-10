from __future__ import annotations

import json
import logging

from services.ai.client import OpenAIClient
from services.ai.policy import admin_marketing_system_prompt

log = logging.getLogger(__name__)


def _fallback_variants(context: str, goal: str, tone: str) -> tuple[str, str]:
    # Детерминированные шаблоны без внешних сервисов.
    clean_goal = goal.strip() or "продолжить пользоваться сервисом"
    clean_context = context.strip() or "человек уже познакомился с Метротерапией"
    a = (
        f"{clean_context}\n\n"
        f"Если вам откликается этот формат, можно сделать следующий шаг: {clean_goal}. "
        "Без спешки — выберите то, что сейчас действительно удобно."
    )
    b = (
        f"{clean_context}\n\n"
        f"Можно продолжить в своём темпе: {clean_goal}. "
        "Метротерапия останется рядом как спокойная аудиопрактика для дороги и повседневного ритма."
    )
    return a, b


def generate_ab_texts(*, context: str, goal: str, tone: str = "спокойный, уважительный, простой") -> tuple[str, str]:
    """Generate two safe text variants for admin-managed funnel steps.

    AI is used only as an admin text helper. It must not act as a therapist,
    diagnose, promise treatment outcomes, or generate medical claims.
    If AI is disabled, no provider is configured, or the request fails,
    deterministic fallback variants are returned.
    """

    client = OpenAIClient.from_settings()
    if not client:
        return _fallback_variants(context, goal, tone)

    system = admin_marketing_system_prompt(task="написать два коротких понятных сообщения для пользователя")
    user = (
        "Напиши два разных варианта сообщения для пользователя Метротерапии.\n\n"
        "Важно:\n"
        "• пиши по-русски, простыми словами;\n"
        "• не используй слова: автоворонка, оффер, лид, конверсия, A/B, сегмент, триггер;\n"
        "• не обещай лечение, терапевтический эффект, диагноз или медицинский результат;\n"
        "• не дави на человека и не пугай;\n"
        "• текст должен звучать как спокойное человеческое сообщение.\n\n"
        f"О чём сообщение:\n{context}\n\n"
        f"Что человек должен сделать после сообщения:\n{goal}\n\n"
        f"Тон:\n{tone}\n\n"
        "Верни строго JSON: {\"A\":\"...\",\"B\":\"...\"}. "
        "Без markdown и без дополнительных пояснений."
    )

    text = client.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.7,
        max_tokens=500,
    )
    if not text:
        return _fallback_variants(context, goal, tone)

    try:
        obj = json.loads(text)
        a = str(obj.get("A", "")).strip()
        b = str(obj.get("B", "")).strip()
        if a and b:
            return a, b
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("ai_copywriter_bad_response", extra={"error_type": type(exc).__name__})

    return _fallback_variants(context, goal, tone)
