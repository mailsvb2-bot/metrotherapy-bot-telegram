from __future__ import annotations

import os

from config.settings import settings
from services.ai.policy import ai_enabled_from_settings
from services.ai.providers.base import AIChatProvider, AIProviderConfig
from services.ai.providers.gigachat import GigaChatProvider
from services.ai.providers.openai_compatible import OpenAICompatibleProvider
from services.ai.providers.yandex import YandexGPTProvider

_DEEPSEEK_PROVIDER_NAMES = {"deepseek", "deepseek-chat", "deepseek-reasoner"}
_OPENAI_COMPAT_PROVIDER_NAMES = {"openai", "openai-compatible", "openai_compatible", "compatible"}


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None and value != "" else default


def _normalized_provider_name(raw: str) -> str:
    selected = (raw or "").strip().lower()
    if selected in _DEEPSEEK_PROVIDER_NAMES:
        return "deepseek"
    if selected in {"sber"}:
        return "gigachat"
    return selected


def provider_name() -> str:
    explicit = _normalized_provider_name(_env("AI_PROVIDER", getattr(settings, "AI_PROVIDER", "")) or "")
    if explicit:
        return explicit
    if _env("DEEPSEEK_API_KEY", "").strip():
        return "deepseek"
    base_url = (getattr(settings, "OPENAI_BASE_URL", "") or _env("OPENAI_BASE_URL", "")).strip().lower()
    if "api.deepseek.com" in base_url:
        return "deepseek"
    if _env("YANDEX_API_KEY", "").strip():
        return "yandex"
    if _env("GIGACHAT_CREDENTIALS", "").strip():
        return "gigachat"
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


def _deepseek_config() -> AIProviderConfig:
    api_key = (_env("DEEPSEEK_API_KEY", "") or getattr(settings, "OPENAI_API_KEY", "") or _env("OPENAI_API_KEY", "")).strip()
    model = (_env("DEEPSEEK_MODEL", "") or getattr(settings, "OPENAI_MODEL", "") or _env("OPENAI_MODEL", "deepseek-chat") or "deepseek-chat").strip()
    base_url = (_env("DEEPSEEK_BASE_URL", "") or getattr(settings, "OPENAI_BASE_URL", "") or _env("OPENAI_BASE_URL", "https://api.deepseek.com/v1") or "https://api.deepseek.com/v1").strip()
    return AIProviderConfig(
        name="deepseek",
        api_key=api_key,
        model=model,
        base_url=base_url,
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


def _gigachat_config() -> AIProviderConfig:
    return AIProviderConfig(
        name="gigachat",
        model=_env("GIGACHAT_MODEL", "GigaChat-2-Pro").strip() or "GigaChat-2-Pro",
        base_url=_env("GIGACHAT_BASE_URL", "https://gigachat.devices.sberbank.ru/api/v1").strip(),
        timeout_sec=_timeout(),
        credentials=_env("GIGACHAT_CREDENTIALS", "").strip(),
        scope=_env("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip() or "GIGACHAT_API_PERS",
    )


def provider_configured(name: str | None = None) -> bool:
    selected = _normalized_provider_name(name or provider_name())
    if not ai_enabled_from_settings():
        return False
    if selected == "yandex":
        cfg = _yandex_config()
        return bool(cfg.api_key and cfg.model and cfg.base_url)
    if selected == "gigachat":
        cfg = _gigachat_config()
        return bool(cfg.credentials and cfg.model and cfg.base_url)
    if selected == "deepseek":
        cfg = _deepseek_config()
        return bool(cfg.api_key and cfg.model and cfg.base_url)
    cfg = _openai_config()
    return bool(cfg.api_key and cfg.model and cfg.base_url)


def build_ai_provider() -> AIChatProvider | None:
    if not ai_enabled_from_settings():
        return None
    selected = provider_name()
    if selected == "yandex":
        cfg = _yandex_config()
        return YandexGPTProvider(cfg) if provider_configured("yandex") else None
    if selected == "gigachat":
        cfg = _gigachat_config()
        return GigaChatProvider(cfg) if provider_configured("gigachat") else None
    if selected == "deepseek":
        cfg = _deepseek_config()
        return OpenAICompatibleProvider(cfg) if provider_configured("deepseek") else None
    if selected in _OPENAI_COMPAT_PROVIDER_NAMES:
        cfg = _openai_config()
        return OpenAICompatibleProvider(cfg) if provider_configured("openai") else None
    return None
