from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from runtime import payment_http


def _request() -> web.Request:
    return make_mocked_request(
        "GET",
        "/pay/yookassa?source=telegram&user_id=407&kind=tokens&package_id=practice_start_7",
    )


@pytest.mark.asyncio
async def test_telegram_yookassa_kill_switch_rejects_existing_links(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")

    def unexpected_checkout(**_kwargs):
        raise AssertionError("provider checkout must not be called")

    monkeypatch.setattr(payment_http, "_create_yookassa_payment", unexpected_checkout)

    response = await payment_http.pay_yookassa_web(_request())

    assert response.status == 410
    assert "временно отключена" in response.text


@pytest.mark.asyncio
async def test_telegram_yookassa_enabled_reaches_provider(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    monkeypatch.setenv("PAYMENT_CHECKOUT_INTENT_REQUIRED", "0")
    monkeypatch.setattr(
        payment_http,
        "_create_yookassa_payment",
        lambda **_kwargs: "https://yookassa.example/confirmation",
    )

    with pytest.raises(web.HTTPFound) as redirect:
        await payment_http.pay_yookassa_web(_request())

    assert redirect.value.location == "https://yookassa.example/confirmation"
