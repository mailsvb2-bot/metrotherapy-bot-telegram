from __future__ import annotations

import os
from typing import Any

from config.settings import settings

AI_ROLE = "admin_marketing_advisor"
AI_ROLE_LABEL_RU = "маркетинговый советчик администратора"
AI_USER_THERAPY_ALLOWED = False

AI_ALLOWED_SCOPES: tuple[str, ...] = (
    "admin_marketing",
    "funnel_copy",
    "funnel_profile_selection",
    "pricing_recommendations",
    "conversion_analysis",
)

AI_FORBIDDEN_SCOPES: tuple[str, ...] = (
    "therapy",
    "psychotherapy",
    "diagnosis",
    "medical_advice",
    "treatment_promises",
    "user_facing_therapeutic_dialogue",
)

AI_ADMIN_MARKETING_SYSTEM_PROMPT = (
    "Вы — AI-помощник маркетолога и администратора Telegram-бота. "
    "Ваша зона ответственности: админские маркетинговые рекомендации, автоворонка, "
    "A/B-тексты, анализ спроса, ценовые рекомендации и улучшение конверсии. "
    "Вы не терапевт, не врач и не психолог. Не ставьте диагнозы, не обещайте лечение, "
    "исцеление, медицинский или гарантированный терапевтический результат. "
    "Не ведите пользовательский терапевтический диалог и не меняйте терапевтический контент. "
    "Любые рекомендации являются советом администратору и не применяются автоматически."
)


def _env_bool(name: str, default: int | str = 1) -> bool:
    raw = os.getenv(name)
    value = str(default if raw is None else raw).strip().lower()
    return value in {"1", "true", "yes", "on"}


def ai_enabled_from_settings() -> bool:
    """Return live AI switch.

    Settings are created at import time, while tests and systemd probes may update
    env later in the same process. Read AI_ENABLED from os.environ first so
    provider detection and health snapshots reflect the active process env.
    """
    if "AI_ENABLED" in os.environ:
        return _env_bool("AI_ENABLED", 1)
    try:
        return int(getattr(settings, "AI_ENABLED", 1) or 0) == 1
    except (TypeError, ValueError):
        return False


def ai_provider_configured() -> bool:
    from services.ai.providers.router import provider_configured

    return provider_configured()


def ai_provider_name() -> str:
    from services.ai.providers.router import provider_name

    return provider_name()


def ai_policy_snapshot() -> dict[str, Any]:
    """Public, secret-safe AI contract for health/admin surfaces."""
    return {
        "ai_role": AI_ROLE,
        "ai_role_label": AI_ROLE_LABEL_RU,
        "ai_enabled": ai_enabled_from_settings(),
        "ai_provider": ai_provider_name(),
        "ai_provider_configured": ai_provider_configured(),
        "ai_user_therapy_allowed": AI_USER_THERAPY_ALLOWED,
        "ai_allowed_scopes": list(AI_ALLOWED_SCOPES),
        "ai_forbidden_scopes": list(AI_FORBIDDEN_SCOPES),
    }


def admin_marketing_system_prompt(*, task: str = "") -> str:
    task = (task or "").strip()
    if not task:
        return AI_ADMIN_MARKETING_SYSTEM_PROMPT
    return f"{AI_ADMIN_MARKETING_SYSTEM_PROMPT}\n\nТекущая задача администратора: {task}"
