from __future__ import annotations

from services.payments import yookassa_checkout


def test_yookassa_checkout_provider_error_body_is_redacted_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")

    assert yookassa_checkout._provider_error_body_for_log("provider-body") == "<redacted in prod>"


def test_yookassa_checkout_provider_error_body_is_limited_in_non_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")

    body = "line1\n" + "x" * 1200
    logged = yookassa_checkout._provider_error_body_for_log(body)

    assert "\n" not in logged
    assert logged.startswith("line1 ")
    assert len(logged) == 1000
