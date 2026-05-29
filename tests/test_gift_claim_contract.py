from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from services.gift_claims import is_gift_token, normalize_gift_token
from services.messenger.package_payment_ui import gift_package_text, package_payment_links


def test_public_payment_ui_has_no_legacy_db_tariff_surface():
    source = Path("services/payments/ui.py").read_text(encoding="utf-8")

    assert "kb_legacy_db_tariffs" not in source
    assert "get_active_plans" not in source
    assert "public_practice_packages" in source


def test_gift_package_links_include_claim_tokens(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")

    links = package_payment_links(user_id=101, platform="vk", external_user_id="101", as_gift=True)

    assert len(links) == 4
    for item in links:
        assert is_gift_token(item.gift_token)
        parsed = urlparse(item.url)
        params = parse_qs(parsed.query)
        assert params["gift"] == ["1"]
        assert params["gift_token"] == [item.gift_token]
        assert params["kind"] == ["tokens"]
        assert params["package_id"] == [item.package_id]


def test_gift_text_contains_claim_commands(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")

    text = gift_package_text(user_id=202, platform="max", external_user_id="202")

    assert "claim gift_" in text
    assert "Получатель может отправить" in text
    assert "morning_5" not in text
    assert "both_20" not in text


def test_normalize_gift_token_accepts_claim_and_start_payloads():
    token = "gift_" + "a" * 32

    assert normalize_gift_token(token) == token
    assert normalize_gift_token(f"claim {token}") == token
    assert normalize_gift_token(f"/start {token}") == token
    assert is_gift_token(token)
