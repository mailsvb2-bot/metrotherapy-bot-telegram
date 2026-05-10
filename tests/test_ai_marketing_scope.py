from config.settings import settings
from handlers.admin_inline_copy import _format_ai_price_recommendations
from services.ai.client import OpenAIClient
from services.ai.policy import ai_policy_snapshot
from services.ai.providers.router import build_ai_provider, provider_configured, provider_name
from services.ai.providers.yandex import YandexGPTProvider
from services.ai_copywriter import generate_ab_texts


def test_openai_client_respects_ai_enabled(monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", 0)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    assert OpenAIClient.from_settings() is None


def test_ai_copywriter_falls_back_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", 0)
    monkeypatch.setenv("AI_ENABLED", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)

    a, b = generate_ab_texts(context="Аудиосессии по расписанию", goal="Мягко предложить подписку")

    assert "Контекст:" in a
    assert "Цель:" in b
    assert "подпис" in (a + b).lower()


def test_ai_provider_router_selects_yandex(monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", 1)
    monkeypatch.setenv("AI_PROVIDER", "yandex")
    monkeypatch.setenv("YANDEX_API_KEY", "test-yandex-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "folder-1")
    monkeypatch.delenv("YANDEX_MODEL", raising=False)

    provider = build_ai_provider()

    assert provider_name() == "yandex"
    assert provider_configured("yandex") is True
    assert isinstance(provider, YandexGPTProvider)
    assert provider.config.model == "gpt://folder-1/yandexgpt/latest"


def test_ai_policy_snapshot_reports_provider_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", 1)
    monkeypatch.setenv("AI_PROVIDER", "yandex")
    monkeypatch.setenv("YANDEX_API_KEY", "test-yandex-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "folder-1")

    snapshot = ai_policy_snapshot()

    assert snapshot["ai_provider"] == "yandex"
    assert snapshot["ai_provider_configured"] is True
    assert "test-yandex-key" not in str(snapshot)
    assert snapshot["ai_user_therapy_allowed"] is False


def test_admin_price_recommendations_render_recommendation_payload():
    payload = {
        "ok": True,
        "snapshot": {"by_scope": {"morning": 2, "evening": 1, "both": 3}},
        "recommendation": {
            "morning": 1.05,
            "evening": 0.95,
            "both": 1.1,
            "comment": "Тестовый комментарий",
        },
    }

    text = _format_ai_price_recommendations(payload)

    assert "AI-рекомендации цен" in text
    assert "маркетинговый советчик" in text
    assert "×1.05" in text
    assert "×0.95" in text
    assert "×1.10" in text
    assert "Цены автоматически не применяются" in text
    assert "Тестовый комментарий" in text
