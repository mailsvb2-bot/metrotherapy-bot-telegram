from pathlib import Path


def test_body_technique_contract_has_no_user_facing_ai_call():
    src = Path("services/body.py").read_text(encoding="utf-8")
    assert "OpenAIClient" not in src
    assert ".chat(" not in src


def test_body_technique_returns_local_text_quickly():
    from services.body import quick_technique

    text = quick_technique("Плечи")
    assert "Мини" in text
    assert "Плечи" in text
    assert "выдох" in text.lower()
