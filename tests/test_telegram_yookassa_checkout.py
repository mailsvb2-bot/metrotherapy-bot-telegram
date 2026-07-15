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
@pytest.mark.parametrize("legacy_switch", ["0", "1"])
async def test_telegram_yookassa_is_rejected_even_if_legacy_switch_is_enabled(monkeypatch, legacy_switch) -> None:
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", legacy_switch)
    monkeypatch.setattr(
        payment_http, "_create_yookassa_payment",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("provider checkout must not be called")),
    )
    response = await payment_http.pay_yookassa_web(_request())
    assert response.status == 410
    assert "только Telegram Stars" in response.text
