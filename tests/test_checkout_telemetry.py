from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest
from aiohttp import web

from runtime import payment_http
from services import checkout_telemetry


async def _direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


def test_record_payment_started_emits_canonical_runtime_event(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        checkout_telemetry,
        "log_runtime_event",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    checkout_telemetry.record_payment_started(
        42,
        provider="telegram_stars",
        source="telegram",
        package_id="practice_start_7",
        amount=1500,
        currency="xtr",
        gift=False,
        transport="invoice_link",
        extra={"campaign": "summer", "provider": "must-not-overwrite"},
    )

    assert len(captured) == 1
    args, kwargs = captured[0]
    assert args == (42,)
    assert kwargs["event_type"] == "payment_started"
    assert kwargs["source"] == "telegram"
    payload = kwargs["payload"]
    assert payload["provider"] == "telegram_stars"
    assert payload["package_id"] == "practice_start_7"
    assert payload["amount"] == 1500
    assert payload["currency"] == "XTR"
    assert payload["transport"] == "invoice_link"
    assert payload["campaign"] == "summer"


def test_record_payment_started_ignores_non_user_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        checkout_telemetry,
        "log_runtime_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not log")),
    )
    checkout_telemetry.record_payment_started(
        0,
        provider="yookassa",
        source="web",
        package_id="practice_start_7",
        amount=249900,
        currency="RUB",
    )


class _Request:
    def __init__(self) -> None:
        self.query = {
            "source": "vk",
            "user_id": "77",
            "external_user_id": "vk-77",
            "package_id": "practice_start_7",
            "kind": "tokens",
        }


@pytest.mark.asyncio
async def test_yookassa_redirect_records_checkout_only_after_provider_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(payment_http.asyncio, "to_thread", _direct_to_thread)
    package = SimpleNamespace(package_id="practice_start_7", price_rub=2499)
    monkeypatch.setattr(payment_http, "package_by_id", lambda _package_id: package)
    monkeypatch.setattr(payment_http, "_checkout_intent_error_response", lambda **_kwargs: None)
    monkeypatch.setattr(
        payment_http,
        "_create_yookassa_payment",
        lambda **_kwargs: "https://yookassa.example/confirm/abc",
    )
    captured: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        payment_http,
        "record_payment_started",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    with pytest.raises(web.HTTPFound) as redirect:
        await payment_http.pay_yookassa_web(_Request())  # type: ignore[arg-type]

    assert redirect.value.location == "https://yookassa.example/confirm/abc"
    assert len(captured) == 1
    args, kwargs = captured[0]
    assert args == (77,)
    assert kwargs["provider"] == "yookassa"
    assert kwargs["source"] == "vk"
    assert kwargs["package_id"] == "practice_start_7"
    assert kwargs["amount"] == 249900
    assert kwargs["currency"] == "RUB"
    assert kwargs["transport"] == "provider_redirect"


@pytest.mark.asyncio
async def test_yookassa_failed_creation_does_not_record_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(payment_http.asyncio, "to_thread", _direct_to_thread)
    package = SimpleNamespace(package_id="practice_start_7", price_rub=2499)
    monkeypatch.setattr(payment_http, "package_by_id", lambda _package_id: package)
    monkeypatch.setattr(payment_http, "_checkout_intent_error_response", lambda **_kwargs: None)
    monkeypatch.setattr(
        payment_http,
        "_create_yookassa_payment",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("provider unavailable")),
    )
    monkeypatch.setattr(
        payment_http,
        "record_payment_started",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not record")),
    )

    response = await payment_http.pay_yookassa_web(_Request())  # type: ignore[arg-type]
    assert response.status == 500
    assert "PAYMENT_CREATE_FAILED" in response.text
