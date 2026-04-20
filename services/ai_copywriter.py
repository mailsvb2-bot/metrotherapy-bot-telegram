from __future__ import annotations
import logging
import urllib.error


import json
import os
import random
import urllib.request


def _fallback_variants(context: str, goal: str, tone: str) -> tuple[str, str]:
    # Детерминированные шаблоны без внешних сервисов.
    base = f"Контекст: {context.strip()}\nЦель: {goal.strip()}\nТон: {tone.strip()}"
    a = (
        f"{base}\n\n"
        "Если Вам подходит — можно продолжить в подписке. "
        "Вы получите регулярные сессии по расписанию и спокойный, накопительный эффект."
    )
    b = (
        f"{base}\n\n"
        "Если Вы хотите, чтобы эффект закреплялся — лучше идти мягко, но регулярно. "
        "Подписка — это удобное расписание и полный доступ."
    )
    return a, b


def generate_ab_texts(*, context: str, goal: str, tone: str = "уважительный, бережный") -> tuple[str, str]:
    """Генерирует два варианта текста A/B.

    Если задан OPENAI_API_KEY — использует OpenAI API (без сторонних библиотек).
    Если ключа нет или запрос не удался — возвращает детерминированные шаблоны.
    """

    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")

    if not api_key:
        return _fallback_variants(context, goal, tone)

    system = (
        "Вы — интеллигентный AI-копирайтер для Telegram-бота. "
        "Пишите уважительно, на 'Вы', без давления, короткими абзацами. "
        "Дайте два разных варианта текста (A и B) для автоворонки."
    )
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
            data=json.dumps(payload).encode("utf-8"),
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
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError):
        logging.getLogger(__name__).exception("ai_copywriter failed")
    except (json.JSONDecodeError, KeyError, IndexError):
        logging.getLogger(__name__).exception("ai_copywriter failed")
    except (ValueError, TypeError):
        logging.getLogger(__name__).exception("ai_copywriter failed")

    # fallback
    return _fallback_variants(context, goal, tone)