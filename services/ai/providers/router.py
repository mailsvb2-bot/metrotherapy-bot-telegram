from __future__ import annotations

import os

from config.settings import settings
from services.ai.policy import ai_enabled_from_settings
from services.ai.providers.base import AIChatProvider, AIProviderConfig
from services.ai.providers.openai_compatible import OpenAICompatibleProvider
from services.ai.providers.yandex import YandexGPTProvider


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None and value != "" else default


def provider_name() -> str:
    explicit = (_env("AI_PROVIDER", getattr(settings, "AI_PROVIDER", "")) or "").strip().lower()
    if explicit:
        return explicit
    if _env("YANDEX_API_KEY", "").strip():
        return "yandex"
    return "openai"


def _timeout() -> int:
    try:
        return int(_env("AI_TIMEOUT_SEC", "20") or "20")
    except ValueError:
        return 20


def _openai_config() -> AIProviderConfig:
    return AIProviderConfig(
        name="openai",
        api_key=(getattr(settings, "OPENAI_API_KEY", "") or _env("OPENAI_API_KEY", "")).strip(),
        model=(getattr(settings, "OPENAI_MODEL", "") or _env("OPENAI_MODEL", "gpt-4.1-mini") or "gpt-4.1-mini").strip(),
        base_url=(getattr(settings, "OPENAI_BASE_URL", "") or _env("OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1").strip(),
        timeout_sec=_timeout(),
    )


def _yandex_config() -> AIProviderConfig:
    folder_id = _env("YANDEX_FOLDER_ID", "").strip()
    model = _env("YANDEX_MODEL", "").strip()
    if not model and folder_id:
        model = f"gpt://{folder_id}/yandexgpt/latest"
    return AIProviderConfig(
        name="yandex",
        api_key=_env("YANDEX_API_KEY", "").strip(),
        model=model or "yandexgpt/latest",
        base_url=_env("YANDEX_BASE_URL", "https://llm.api.cloud.yandex.net/v1").strip(),
        timeout_sec=_timeout(),
        folder_id=folder_id,
    )


def provider_configured(name: str | None = None) -> bool:
    selected = (name or provider_name()).strip().lower()
    if not ai_enabled_from_settings():
        return False
    cfg = _yandex_config() if selected == "yandex" else _openai_config()
    return bool(cfg.api_key and cfg.model and cfg.base_url)


def build_ai_provider() -> AIChatProvider | None:
    if not ai_enabled_from_settings():
        return None
    selected = provider_name()
    if selected == "yandex":
        cfg = _yandex_config()
        return YandexGPTProvider(cfg) if provider_configured("yandex") else None
    if selected in {"openai", "openai-compatible", "openai_compatible", "compatible"}:
        cfg = _openai_config()
        return OpenAICompatibleProvider(cfg) if provider_configured("openai") else None
    return None
