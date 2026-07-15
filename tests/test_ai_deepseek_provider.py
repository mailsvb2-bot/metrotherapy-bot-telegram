from __future__ import annotations

import json
import urllib.error

from services.ai.providers.base import AIProviderConfig
from services.ai.providers.openai_compatible import OpenAICompatibleProvider
from services.ai.providers import router


def test_deepseek_openai_compatible_provider_disables_thinking_by_default(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.delenv("OPENAI_THINKING", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    provider = OpenAICompatibleProvider(
        AIProviderConfig(
            name="openai",
            api_key="deepseek-test-key",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            timeout_sec=7,
        )
    )

    assert provider.chat([{"role": "user", "content": "hello"}]) == "ok"

    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["timeout"] == 7
    assert captured["payload"]["model"] == "deepseek-chat"
    assert captured["payload"]["thinking"] == {"type": "disabled"}


def test_deepseek_openai_compatible_provider_can_enable_thinking(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("OPENAI_THINKING", "enabled")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    provider = OpenAICompatibleProvider(
        AIProviderConfig(
            name="openai",
            api_key="deepseek-test-key",
            model="deepseek-reasoner",
            base_url="https://api.deepseek.com/v1",
        )
    )

    assert provider.chat([{"role": "user", "content": "hello"}]) == "ok"
    assert "thinking" not in captured["payload"]


def test_deepseek_provider_alias_uses_dedicated_env(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "1")
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    assert router.provider_name() == "deepseek"
    assert router.provider_configured("deepseek") is True

    provider = router.build_ai_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.config.name == "deepseek"
    assert provider.config.model == "deepseek-chat"
    assert provider.config.base_url == "https://api.deepseek.com/v1"


def test_deepseek_provider_auto_detects_deepseek_key(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "1")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    assert router.provider_name() == "deepseek"
    assert router.provider_configured() is True


def test_deepseek_defaults_never_inherit_openai_endpoint_or_model(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "1")
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    provider = router.build_ai_provider()

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.config.name == "deepseek"
    assert provider.config.model == "deepseek-chat"
    assert provider.config.base_url == "https://api.deepseek.com/v1"


def test_explicit_deepseek_requires_dedicated_key(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "1")
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-reused")

    assert router.provider_configured("deepseek") is False
    assert router.build_ai_provider() is None


def test_openai_compatible_provider_returns_none_on_http_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "bad request", hdrs=None, fp=None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    provider = OpenAICompatibleProvider(
        AIProviderConfig(
            name="openai",
            api_key="test-key",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
        )
    )

    assert provider.chat([{"role": "user", "content": "hello"}]) is None
