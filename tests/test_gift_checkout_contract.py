from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from services.db import db
from services.payments.checkout_intent import verify_checkout_intent
from services.payments.ui import kb_gift_tariffs
from services.practice_token_contract import public_practice_packages


def _button_urls(markup) -> list[str]:
    urls: list[str] = []
    for row in markup.inline_keyboard:
        for button in row:
            url = getattr(button, "url", None)
            if url:
                urls.append(str(url))
    return urls


def test_gift_tariff_buttons_create_buyer_bound_gift_tokens(monkeypatch):
    user_id = 910100
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-signing-key")

    with db() as conn:
        conn.execute("DELETE FROM gift_claims WHERE buyer_user_id=?", (user_id,))

    markup = kb_gift_tariffs(user_id=user_id, back_cb="menu:main")
    urls = _button_urls(markup)
    packages = public_practice_packages()

    assert len(urls) == len(packages)
    try:
        for url, package in zip(urls, packages):
            params = parse_qs(urlsplit(url).query)
            gift_token = params["gift_token"][0]
            intent = params["intent"][0]

            assert params["user_id"] == [str(user_id)]
            assert params["package_id"] == [package.package_id]
            assert params["kind"] == ["tokens"]
            assert params["gift"] == ["1"]
            assert gift_token.startswith("gift_")

            payload = verify_checkout_intent(
                intent,
                expected_user_id=user_id,
                expected_package_id=package.package_id,
                expected_kind="tokens",
                expected_gift_token=gift_token,
            )
            assert payload["gift_token"] == gift_token

            with db() as conn:
                row = conn.execute(
                    """
                    SELECT buyer_user_id, package_id, status, source_platform
                    FROM gift_claims
                    WHERE gift_token=?
                    """.strip(),
                    (gift_token,),
                ).fetchone()

            assert row is not None
            assert int(row["buyer_user_id"]) == user_id
            assert row["package_id"] == package.package_id
            assert row["status"] == "created"
            assert row["source_platform"] == "telegram"
    finally:
        with db() as conn:
            conn.execute("DELETE FROM gift_claims WHERE buyer_user_id=?", (user_id,))
