from __future__ import annotations

import pytest
from aiohttp.test_utils import make_mocked_request

from runtime.payment_http import payment_terms_web


@pytest.mark.asyncio
async def test_payment_terms_web_is_a_real_utf8_page(monkeypatch) -> None:
    monkeypatch.setenv("PAYMENT_MERCHANT_NAME", "Метротерапия")
    monkeypatch.setenv("PAYMENT_SUPPORT_CONTACT", "@metrotherapysupportbot")

    response = await payment_terms_web(make_mocked_request("GET", "/terms"))

    assert response.status == 200
    assert response.content_type == "text/html"
    assert response.charset == "utf-8"
    assert "Условия оплаты цифровых пакетов" in response.text
    assert "звёздами Telegram" in response.text
    assert "XTR" not in response.text
    assert "RUB" not in response.text
