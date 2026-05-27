from __future__ import annotations

from services.payments.ui import kb_tariffs


def _button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _button_urls(markup) -> list[str]:
    return [button.url for row in markup.inline_keyboard for button in row if button.url]


def test_public_tariff_keyboard_uses_canonical_practice_packages(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")

    markup = kb_tariffs(user_id=404)
    texts = _button_texts(markup)
    urls = _button_urls(markup)

    assert "\u0421\u0442\u0430\u0440\u0442\u043e\u0432\u044b\u0439 \u043f\u0430\u043a\u0435\u0442 \u2014 1 900 \u20bd" in texts
    assert "\u041f\u043e\u043b\u043d\u044b\u0439 \u043c\u0430\u0440\u0448\u0440\u0443\u0442 \u2014 7 900 \u20bd" in texts
    assert "\u0410\u043d\u0442\u0438\u0441\u0442\u0440\u0435\u0441\u0441-\u0441\u0438\u0441\u0442\u0435\u043c\u0430 \u2014 12 900 \u20bd" in texts
    assert "\u041f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u043c\u0435\u0441\u044f\u0446 \u2014 23 000 \u20bd" in texts

    joined = "\n".join(texts + urls)
    assert "morning_5" not in joined
    assert "morning_20" not in joined
    assert "evening_5" not in joined
    assert "evening_20" not in joined
    assert "both_5" not in joined
    assert "both_20" not in joined

    assert any("kind=tokens" in url and "package_id=practice_start_7" in url for url in urls)
    assert any("kind=tokens" in url and "package_id=practice_60" in url for url in urls)
    assert any("kind=tokens" in url and "package_id=practice_antistress_60" in url for url in urls)
    assert any("kind=tokens" in url and "package_id=practice_personal_month" in url for url in urls)
