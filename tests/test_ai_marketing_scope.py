from config.settings import settings
from handlers.admin_inline_copy import _format_ai_price_recommendations
from services.ai.client import OpenAIClient
from services.ai_copywriter import generate_ab_texts


def test_openai_client_respects_ai_enabled(monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", 0)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    assert OpenAIClient.from_settings() is None


def test_ai_copywriter_falls_back_when_disabled(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    a, b = generate_ab_texts(context="Аудиосессии по расписанию", goal="Мягко предложить подписку")

    assert "Контекст:" in a
    assert "Цель:" in b
    assert "подпис" in (a + b).lower()


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
