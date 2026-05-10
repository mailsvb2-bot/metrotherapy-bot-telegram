from __future__ import annotations

from services.ai.providers.base import AIProviderConfig
from services.ai.providers.openai_compatible import OpenAICompatibleProvider


class YandexGPTProvider(OpenAICompatibleProvider):
    """Yandex AI Studio adapter through its OpenAI-compatible endpoint."""

    def __init__(self, config: AIProviderConfig) -> None:
        headers: dict[str, str] = {}
        if config.folder_id:
            # Yandex OpenAI-compatible examples use the project/folder id header.
            headers["OpenAI-Project"] = config.folder_id
        super().__init__(config, extra_headers=headers)
