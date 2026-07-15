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
async def test_telegram_yookassa_creates_provider_checkout_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return "https://yookassa.example/confirmation"

    monkeypatch.setattr(payment_http, "_create_yookassa_payment", _create)

    with pytest.raises(web.HTTPFound) as redirect:
        await payment_http.pay_yookassa_web(_request())

    assert redirect.value.location == "https://yookassa.example/confirmation"
    assert captured["source"] == "telegram"
    assert captured["user_id"] == "407"
    assert captured["package_id"] == "practice_start_7"
    assert captured["kind"] == "tokens"


@pytest.mark.asyncio
async def test_telegram_yookassa_kill_switch_rejects_existing_checkout_link(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")
    monkeypatch.setattr(
        payment_http,
        "_create_yookassa_payment",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("provider checkout must not be called")),
    )

    response = await payment_http.pay_yookassa_web(_request())
    assert response.status == 410
    assert "временно отключена" in response.text
