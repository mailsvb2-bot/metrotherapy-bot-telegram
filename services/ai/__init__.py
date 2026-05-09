"""AI services.

Contract:
- services.ai.client: low-level OpenAI HTTP client
- services.ai.decisions: admin/marketing funnel advice
- services.ai.pricing: admin-only price recommendations

AI here is an admin/marketing assistant. It must not act as a therapist,
make medical claims, diagnose users, or change user-facing therapeutic content.
No third-party SDK dependencies.
"""

from services.ai.client import OpenAIClient
from services.ai.decisions import choose_funnel_profile, choose_funnel_profile_async, record_funnel_profile
from services.ai.pricing import recommend_prices, record_price_recommendation

__all__ = [
    "OpenAIClient",
    "choose_funnel_profile",
    "choose_funnel_profile_async",
    "record_funnel_profile",
    "recommend_prices",
    "record_price_recommendation",
]
