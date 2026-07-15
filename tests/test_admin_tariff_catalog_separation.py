from __future__ import annotations

from handlers.admin_tariffs.ui import _prices_text


def test_admin_tariffs_show_canonical_packages_separately_from_legacy_plans(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "buyer_parity")
    monkeypatch.setenv("TELEGRAM_STARS_BUYER_RUB_PER_XTR", "1.54905")

    text = _prices_text()

    assert "Публичные пакеты практик (канонический каталог)" in text
    assert "Стартовый пакет — 1900 ₽ / 1226 XTR" in text
    assert "Архивные тарифы подписки (не управляют публичными пакетами)" in text
