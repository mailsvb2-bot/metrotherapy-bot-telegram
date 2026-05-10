from __future__ import annotations

import asyncio
from dataclasses import dataclass

from services.ai.providers.router import build_ai_provider


@dataclass
class OpenAIClient:
    """Backward-compatible facade for existing imports.

    The implementation now routes through the canonical AI provider layer selected
    by AI_PROVIDER. Existing modules can keep importing OpenAIClient while the
    project is no longer hard-bound to OpenAI as the only provider.
    """

    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout_sec: int = 20

    @classmethod
    def from_settings(cls) -> "OpenAIClient | None":
        provider = build_ai_provider()
        if provider is None:
            return None
        cfg = provider.config
        return cls(api_key=cfg.api_key or cfg.credentials, model=cfg.model, base_url=cfg.base_url, timeout_sec=cfg.timeout_sec)

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 300) -> str | None:
        provider = build_ai_provider()
        if provider is None:
            return None
        return provider.chat(messages, temperature=temperature, max_tokens=max_tokens)

    async def achat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 300) -> str | None:
        return await asyncio.to_thread(self.chat, messages, temperature=temperature, max_tokens=max_tokens)
