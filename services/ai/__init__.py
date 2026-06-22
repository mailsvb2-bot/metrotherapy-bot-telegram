"""AI services.

Contract:
- services.ai.policy: single canonical AI role/scope contract
- services.ai.providers: canonical provider selection and transport adapters
- services.ai.client: backward-compatible facade routed through providers
- services.ai.decisions: admin/marketing funnel advice
- services.ai.pricing: admin-only price recommendations

AI here is an admin/marketing assistant. It must not act as a therapist,
make medical claims, diagnose users, or change user-facing therapeutic content.
No third-party SDK dependencies.
"""

from services.ai.client import OpenAIClient
from services.ai.decisions import choose_funnel_profile, choose_funnel_profile_async, record_funnel_profile
from services.ai.policy import (
    AI_ALLOWED_SCOPES,
    AI_FORBIDDEN_SCOPES,
    AI_ROLE,
    AI_ROLE_LABEL_RU,
    AI_USER_THERAPY_ALLOWED,
    admin_marketing_system_prompt,
    ai_enabled_from_settings,
    ai_policy_snapshot,
    ai_provider_configured,
    ai_provider_name,
)
from services.ai.pricing import recommend_prices, record_price_recommendation
from services.ai.providers import build_ai_provider

__all__ = [
    "AI_ALLOWED_SCOPES",
    "AI_FORBIDDEN_SCOPES",
    "AI_ROLE",
    "AI_ROLE_LABEL_RU",
    "AI_USER_THERAPY_ALLOWED",
    "OpenAIClient",
    "admin_marketing_system_prompt",
    "ai_enabled_from_settings",
    "ai_policy_snapshot",
    "ai_provider_configured",
    "ai_provider_name",
    "build_ai_provider",
    "choose_funnel_profile",
    "choose_funnel_profile_async",
    "record_funnel_profile",
    "recommend_prices",
    "record_price_recommendation",
]
