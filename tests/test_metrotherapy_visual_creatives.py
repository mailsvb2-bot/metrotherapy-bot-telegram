from services.metrotherapy_visual_creatives import build_metrotherapy_visual_brief, visual_wait_seconds


def test_metrotherapy_brief_is_marketing_only():
    brief = build_metrotherapy_visual_brief(concept="ночной дождь", kind="video", country_code="RU")
    assert brief.kind == "video"
    assert brief.aspect_ratio == "9:16"
    assert "non-clinical" in brief.prompt
    assert "guaranteed treatment" in brief.prompt


def test_visual_wait_seconds_fails_safe(monkeypatch):
    monkeypatch.setenv("VISUAL_TELEGRAM_WAIT_SECONDS", "bad")
    assert visual_wait_seconds() == 20
    monkeypatch.setenv("VISUAL_TELEGRAM_WAIT_SECONDS", "1000")
    assert visual_wait_seconds() == 60
