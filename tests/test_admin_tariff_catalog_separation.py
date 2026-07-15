from __future__ import annotations

from handlers.admin_tariffs.ui import _prices_text


def test_admin_tariffs_show_canonical_packages_separately_from_legacy_plans(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")

    text = _prices_text()

    assert "Публичные пакеты практик (канонический каталог)" in text
    assert "Стартовый пакет — 2499 ₽ / 1500 XTR" in text
    assert "Полный маршрут — 4199 ₽ / 2500 XTR" in text
    assert "Антистресс-система — 8290 ₽ / 5000 XTR" in text
    assert "Персональный месяц — 24870 ₽ / 15000 XTR" in text
    assert "Архивные тарифы подписки (не управляют публичными пакетами)" in text
