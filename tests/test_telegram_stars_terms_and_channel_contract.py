from __future__ import annotations

from pathlib import Path

from services.messenger.package_payment_ui import package_payment_links
from services.payments.terms import payment_terms_html, payment_terms_keyboard, payment_terms_text, payment_terms_url
from services.payments.ui import kb_tariffs, kb_telegram_payment_methods

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
    assert "YooKassa" in text
    assert "внешней" in text
    assert "не равна одному рублю" in text
    assert "/paysupport" in text
    assert "@support_example" in text
    assert "https://metrotherapy.example/terms" in text
    assert any(button.callback_data == "stars:buy:practice_start_7" for button in buttons)
    assert any(button.url == "https://metrotherapy.example/terms" for button in buttons)


def test_terms_url_defaults_to_canonical_payment_host(monkeypatch) -> None:
    monkeypatch.delenv("PAYMENT_TERMS_URL", raising=False)
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://pay.metrotherapy.example/")
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://messenger.metrotherapy.example")

    assert payment_terms_url() == "https://pay.metrotherapy.example/terms"


def test_terms_url_replaces_known_legacy_dead_url(monkeypatch) -> None:
    monkeypatch.setenv("PAYMENT_TERMS_URL", "https://metrotherapy.ru/terms")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy-bot.metrotherapy.ru")

    assert payment_terms_url() == "https://metrotherapy-bot.metrotherapy.ru/terms"


def test_full_terms_page_discloses_star_pricing_and_support(monkeypatch) -> None:
    monkeypatch.setenv("PAYMENT_MERCHANT_NAME", "ООО Тест")
    monkeypatch.setenv("PAYMENT_SUPPORT_CONTACT", "@support_example")

    page = payment_terms_html()

    assert "ООО Тест" in page
    assert "Одна Star не равна одному рублю" in page
    assert "YooKassa" in page
    assert "@support_example" in page
    assert "/paysupport" in page


def test_telegram_tariffs_use_intermediate_payment_method_step(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://pay.example")

    buttons = _buttons(kb_tariffs(user_id=99101))
    assert not any(button.url for button in buttons)
    assert any(button.callback_data == "pay:methods:practice_start_7" for button in buttons)

    methods = _buttons(
        kb_telegram_payment_methods(
            user_id=99101,
            package_id="practice_start_7",
        )
    )
    assert any(button.callback_data == "stars:terms:practice_start_7" for button in methods)
    assert any(button.url and "/pay/yookassa?" in str(button.url) for button in methods)


def test_vk_and_max_keep_yookassa_checkout(monkeypatch) -> None:
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://pay.example")

    vk_links = package_payment_links(user_id=99102, platform="vk", external_user_id="vk-99102")
    max_links = package_payment_links(user_id=99103, platform="max", external_user_id="max-99103")

    assert len(vk_links) == 4
    assert len(max_links) == 4
    assert all("/pay/yookassa?" in item.url for item in vk_links)
    assert all("/pay/yookassa?" in item.url for item in max_links)


def test_payment_router_has_terms_and_explicit_payment_method_choice() -> None:
    source = (ROOT / "handlers" / "payments.py").read_text(encoding="utf-8")
    assert 'Command("terms")' in source
    assert 'Command("paysupport")' in source
    assert "pay:gift_methods:" in source
    assert "pay:methods:" in source
    assert "yookassa:gift:" in source
    assert "stars:gift_terms:" in source
    assert "stars:terms:" in source
