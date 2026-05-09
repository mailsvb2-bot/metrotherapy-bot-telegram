from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

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


def _ai_enabled() -> bool:
    try:
        return int(os.getenv("AI_ENABLED", "1") or "0") == 1
    except ValueError:
        return False


def generate_ab_texts(*, context: str, goal: str, tone: str = "уважительный, бережный") -> tuple[str, str]:
    """Generate two A/B marketing texts for admin-managed funnel steps.

    AI is used only as an admin/marketing assistant. It must not act as a therapist,
    diagnose, promise treatment outcomes, or generate medical claims.
    If AI is disabled, no key is configured, or the request fails, deterministic
    fallback variants are returned.
    """

    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    base_url = os.getenv("OPENAI_BASE_URL", os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"))

    if not _ai_enabled() or not api_key:
        return _fallback_variants(context, goal, tone)

    system = admin_marketing_system_prompt(task="написать два A/B текста автоворонки")
    user = (
        f"Контекст продукта:\n{context}\n\n"
        f"Цель сообщения:\n{goal}\n\n"
        f"Тон:\n{tone}\n\n"
        "Сформируйте ответ строго в JSON: {\"A\":\"...\",\"B\":\"...\"}. "
        "Без лишних ключей и без markdown."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": float(os.getenv("OPENAI_TEMPERATURE", "0.8")),
        "response_format": {"type": "json_object"},
    }

    try:
        req = urllib.request.Request(
            url=base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        obj = json.loads(content)
        a = str(obj.get("A", "")).strip()
        b = str(obj.get("B", "")).strip()
        if a and b:
            return a, b
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        log.warning("ai_copywriter_transport_failed", extra={"error_type": type(exc).__name__})
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("ai_copywriter_bad_response", extra={"error_type": type(exc).__name__})

    return _fallback_variants(context, goal, tone)
