from __future__ import annotations

import json
import logging

from services.ai.client import OpenAIClient
from services.ai.policy import admin_marketing_system_prompt

log = logging.getLogger(__name__)


def _fallback_variants(context: str, goal: str, tone: str) -> tuple[str, str]:
    # Детерминированные шаблоны без внешних сервисов.
    base = f"Контекст: {context.strip()}\nЦель: {goal.strip()}\nТон: {tone.strip()}"
    a = (
        f"{base}\n\n"
        "Если Вам подходит — можно продолжить в подписке. "
        "Вы получите регулярные аудиосессии по расписанию и спокойный формат сопровождения."
    )
    b = (
        f"{base}\n\n"
        "Если Вы хотите пользоваться сервисом регулярно — удобнее оформить подписку. "
        "Она открывает доступ к расписанию и полному набору аудиосессий."
    )
    return a, b


def generate_ab_texts(*, context: str, goal: str, tone: str = "уважительный, бережный") -> tuple[str, str]:
    """Generate two A/B marketing texts for admin-managed funnel steps.

    AI is used only as an admin/marketing assistant. It must not act as a therapist,
    diagnose, promise treatment outcomes, or generate medical claims.
    If AI is disabled, no provider is configured, or the request fails,
    deterministic fallback variants are returned.
    """

    client = OpenAIClient.from_settings()
    if not client:
        return _fallback_variants(context, goal, tone)

    system = admin_marketing_system_prompt(task="написать два A/B текста автоворонки")
    user = (
        f"Контекст продукта:\n{context}\n\n"
        f"Цель сообщения:\n{goal}\n\n"
        f"Тон:\n{tone}\n\n"
        "Сформируйте ответ строго в JSON: {\"A\":\"...\",\"B\":\"...\"}. "
        "Без лишних ключей и без markdown."
    )

    text = client.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.8,
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
