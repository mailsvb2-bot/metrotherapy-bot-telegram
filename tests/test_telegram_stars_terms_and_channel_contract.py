from __future__ import annotations

from pathlib import Path

from services.messenger.package_payment_ui import package_payment_links
from services.payments.terms import payment_terms_keyboard, payment_terms_text
from services.payments.ui import kb_tariffs

ROOT = Path(__file__).resolve().parents[1]


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_terms_surface_requires_explicit_acceptance(monkeypatch) -> None:
    monkeypatch.setenv("PAYMENT_TERMS_URL", "https://metrotherapy.example/terms")
    monkeypatch.setenv("PAYMENT_SUPPORT_CONTACT", "@support_example")

    text = payment_terms_text()
    markup = payment_terms_keyboard(package_id="practice_start_7", as_gift=False)
    buttons = _buttons(markup)

    assert "Telegram Stars (XTR)" in text
    assert "/paysupport" in text
    assert "@support_example" in text
    assert "https://metrotherapy.example/terms" in text
    assert any(button.callback_data == "stars:buy:practice_start_7" for button in buttons)
    assert any(button.url == "https://metrotherapy.example/terms" for button in buttons)


def test_telegram_tariffs_never_expose_external_checkout(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://pay.example")

    buttons = _buttons(kb_tariffs(user_id=99101))
    assert not any(button.url for button in buttons)
    assert all("YooKassa" not in str(button.text) for button in buttons)
    assert any(button.callback_data == "stars:terms:practice_start_7" for button in buttons)


def test_vk_and_max_keep_yookassa_checkout(monkeypatch) -> None:
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://pay.example")

    vk_links = package_payment_links(user_id=99102, platform="vk", external_user_id="vk-99102")
    max_links = package_payment_links(user_id=99103, platform="max", external_user_id="max-99103")

    assert len(vk_links) == 4
    assert len(max_links) == 4
    assert all("/pay/yookassa?" in item.url for item in vk_links)
    assert all("/pay/yookassa?" in item.url for item in max_links)


def test_payment_router_has_terms_and_no_telegram_yookassa_copy() -> None:
    source = (ROOT / "handlers" / "payments.py").read_text(encoding="utf-8")
    assert 'Command("terms")' in source
    assert 'Command("paysupport")' in source
    assert "stars:gift_terms:" in source
    assert "stars:terms:" in source
    assert "YooKassa продолжает работать" not in source
