from __future__ import annotations

from aiohttp.test_utils import make_mocked_request

from runtime import payment_http


def test_yookassa_webhook_query_auth_rejected_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("YOOKASSA_WEBHOOK_SECRET", "fixture-value")

    request = make_mocked_request(
        "POST",
        "/pay/yookassa/webhook?secret=fixture-value",
        headers={},
    )

    assert payment_http._webhook_secret_ok(request) is False


def test_yookassa_webhook_header_auth_allowed_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("YOOKASSA_WEBHOOK_SECRET", "fixture-value")

    request = make_mocked_request(
        "POST",
        "/pay/yookassa/webhook",
        headers={"X-Metrotherapy-Webhook-Secret": "fixture-value"},
    )

    assert payment_http._webhook_secret_ok(request) is True


def test_yookassa_webhook_query_auth_kept_for_non_prod_compat(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("YOOKASSA_WEBHOOK_SECRET", "fixture-value")

    request = make_mocked_request(
        "POST",
        "/pay/yookassa/webhook?secret=fixture-value",
        headers={},
    )

    assert payment_http._webhook_secret_ok(request) is True
