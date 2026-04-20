"""AI services.

Контракт (Установка A):
- services.ai.client: низкоуровневый HTTP-клиент
- services.ai.decisions: выбор сценариев/воронок
- services.ai.pricing: рекомендации цен

Никаких сторонних библиотек.
"""

from services.ai.client import OpenAIClient
from services.ai.decisions import choose_funnel_profile, record_funnel_profile
from services.ai.pricing import recommend_prices, record_price_recommendation

__all__ = [
    "OpenAIClient",
    "choose_funnel_profile",
    "record_funnel_profile",
    "recommend_prices",
    "record_price_recommendation",
]
