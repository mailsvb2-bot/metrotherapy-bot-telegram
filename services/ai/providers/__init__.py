"""Canonical AI provider adapters.

AI providers are admin/marketing-only infrastructure. They must not bypass
services.ai.policy and must not be used for user-facing therapeutic dialogue.
"""

from services.ai.providers.base import AIChatProvider, AIProviderConfig, AIProviderError
from services.ai.providers.router import build_ai_provider, provider_configured, provider_name

__all__ = [
    "AIChatProvider",
    "AIProviderConfig",
    "AIProviderError",
    "build_ai_provider",
    "provider_configured",
    "provider_name",
]
