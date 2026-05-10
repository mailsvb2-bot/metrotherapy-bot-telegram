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

    joined = (a + "\n" + b).lower()
    assert "аудиосессии по расписанию" in joined
    assert "подпис" in joined
    assert "контекст:" not in joined
    assert "цель:" not in joined
    assert "оффер" not in joined
    assert "воронк" not in joined


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

    assert "Подсказка по ценам" in text
    assert "Цены сами не меняются" in text
    assert "Утренние практики" in text
    assert "Вечерние практики" in text
    assert "Утро и вечер вместе" in text
    assert "поднять примерно на 5%" in text
    assert "снизить примерно на 5%" in text
    assert "поднять примерно на 10%" in text
    assert "Тестовый комментарий" in text
    assert "AI-рекомендации" not in text
    assert "маркетинговый советчик" not in text
    assert "×1." not in text
