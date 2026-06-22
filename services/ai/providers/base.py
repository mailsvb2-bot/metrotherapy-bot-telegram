from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AIProviderConfig:
    name: str
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout_sec: int = 20
    folder_id: str = ""
    credentials: str = ""
    scope: str = ""


class AIProviderError(RuntimeError):
    """Recoverable AI provider failure.

    Callers must fallback safely and never expose provider internals to users.
    """


class AIChatProvider(Protocol):
    config: AIProviderConfig

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 300) -> str | None:
        """Return assistant text or None on recoverable provider failure."""
