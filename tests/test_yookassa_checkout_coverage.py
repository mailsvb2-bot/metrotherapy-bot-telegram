from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace
from typing import Any

import pytest

from services.payments import yookassa_checkout


def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APP_ENV", "ALLOW_STATIC_PAYMENT_IDEMPOTENCE_KEY_IN_PROD",
        "PAYMENT_IDEMPOTENCE_KEY", "YOOKASSA_IDEMPOTENCE_KEY",
        "YOOKASSA_RECEIPT_EMAIL", "PAYMENT_RECEIPT_EMAIL", "ADMIN_EMAIL",
        "YOOKASSA_TAX_SYSTEM_CODE", "YOOKASSA_VAT_CODE",
        "YOOKASSA_PAYMENT_MODE", "YOOKASSA_PAYMENT_SUBJECT",
        "PAYMENT_AMOUNT_RUB", "GIFT_PAYMENT_AMOUNT_RUB",
        "PAYMENT_DESCRIPTION", "GIFT_PAYMENT_DESCRIPTION",
        "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY",
        "PAYMENT_RETURN_URL", "SITE_PUBLIC_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_env_redaction_intent_and_idempotence(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    assert yookassa_checkout._env_value("MISSING", " value ") == "value"
    assert yookassa_checkout._is_prod() is False
    assert yookassa_checkout._truthy_env("FLAG") is False
    assert yookassa_checkout._provider_error_body_for_log("one\ntwo") == "one two"
    assert yookassa_checkout._explicit_idempotence_key_allowed() is True
    assert yookassa_checkout._checkout_intent_id(None) is None
    assert yookassa_checkout._checkout_intent_id(".signature") is None
    intent = yookassa_checkout._checkout_intent_id("body.signature")
    assert intent is not None and intent.startswith("ci_") and len(intent) == 43
    assert intent == yookassa_checkout._checkout_intent_id("body.other")

    monkeypatch.setenv("APP_ENV", "production")
    assert yookassa_checkout._is_prod() is True
    assert yookassa_checkout._provider_error_body_for_log("secret") == "<redacted in prod>"
    assert yookassa_checkout._explicit_idempotence_key_allowed() is False
    monkeypatch.setenv("ALLOW_STATIC_PAYMENT_IDEMPOTENCE_KEY_IN_PROD", "yes")
    assert yookassa_checkout._explicit_idempotence_key_allowed() is True

    monkeypatch.setenv("PAYMENT_IDEMPOTENCE_KEY", "x" * 200)
    assert len(yookassa_checkout._idempotence_key(
        source="telegram", external_user_id="1", kind="tokens", amount_value="10.00"
    )) == 128
    monkeypatch.delenv("ALLOW_STATIC_PAYMENT_IDEMPOTENCE_KEY_IN_PROD")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="forbidden in prod"):
        yookassa_checkout._idempotence_key(
            source="telegram", external_user_id="1", kind="tokens", amount_value="10.00"
        )
    monkeypatch.delenv("PAYMENT_IDEMPOTENCE_KEY")
    assert yookassa_checkout._idempotence_key(
        source="telegram", external_user_id="1", kind="tokens",
        amount_value="10.00", intent_id="ci_123",
    ) == "metrotherapy:ci_123"
    monkeypatch.setattr(yookassa_checkout.uuid, "uuid4", lambda: "random-key")
    assert yookassa_checkout._idempotence_key(
        source="telegram", external_user_id="1", kind="tokens", amount_value="10.00"
    ) == "random-key"


def test_receipt_helpers_and_legacy_amounts(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    assert yookassa_checkout._receipt_customer_email() == "support@metrotherapy.ru"
    monkeypatch.setenv("ADMIN_EMAIL", " admin@example.test ")
    assert yookassa_checkout._receipt_customer_email() == "admin@example.test"
    monkeypatch.delenv("ADMIN_EMAIL")
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="required in prod"):
        yookassa_checkout._receipt_customer_email()

    monkeypatch.setenv("TEST_INT", "4")
    assert yookassa_checkout._receipt_int("TEST_INT", 2, minimum=1, maximum=5) == 4
    monkeypatch.setenv("TEST_INT", "bad")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="integer"):
        yookassa_checkout._receipt_int("TEST_INT", 2, minimum=1, maximum=5)
    monkeypatch.setenv("TEST_INT", "9")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="between"):
        yookassa_checkout._receipt_int("TEST_INT", 2, minimum=1, maximum=5)

    clear_env(monkeypatch)
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "buyer@example.test")
    monkeypatch.setattr(
        yookassa_checkout, "validate_receipt_contract",
        lambda **_kwargs: (2, 1, "full_payment", "service"),
    )
    receipt = yookassa_checkout.build_yookassa_receipt(
        amount_value="12.00", description="x" * 200
    )
    assert receipt["tax_system_code"] == 2
    assert len(receipt["items"][0]["description"]) == 128

    def invalid(**_kwargs: Any) -> Any:
        raise ValueError("bad receipt")

    monkeypatch.setattr(yookassa_checkout, "validate_receipt_contract", invalid)
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="bad receipt"):
        yookassa_checkout.build_yookassa_receipt(amount_value="1.00", description="x")

    clear_env(monkeypatch)
    assert yookassa_checkout._legacy_amount_description("subscription") == (
        "990.00", "Metrotherapy access"
    )
    monkeypatch.setenv("PAYMENT_AMOUNT_RUB", "10,5")
    monkeypatch.setenv("PAYMENT_DESCRIPTION", "Access")
    assert yookassa_checkout._legacy_amount_description("subscription") == ("10.50", "Access")
    monkeypatch.setenv("GIFT_PAYMENT_AMOUNT_RUB", "20")
    monkeypatch.setenv("GIFT_PAYMENT_DESCRIPTION", "Gift")
    assert yookassa_checkout._legacy_amount_description("gift") == ("20.00", "Gift")
    monkeypatch.setenv("GIFT_PAYMENT_AMOUNT_RUB", "invalid")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="Invalid payment"):
        yookassa_checkout._legacy_amount_description("gift")


class Response:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.payload.encode()


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "buyer@example.test")
    monkeypatch.setattr(
        yookassa_checkout, "build_yookassa_receipt",
        lambda *, amount_value, description: {"amount": amount_value, "description": description},
    )


def test_checkout_credentials_success_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="SHOP_ID"):
        yookassa_checkout.create_yookassa_confirmation_url(source="telegram", external_user_id="1")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="SECRET_KEY"):
        yookassa_checkout.create_yookassa_confirmation_url(source="telegram", external_user_id="1")

    configure(monkeypatch)
    package = SimpleNamespace(package_id="pack", tokens=12, price_rub=345, title="12 практик")
    monkeypatch.setattr(yookassa_checkout, "package_by_id", lambda _package_id: package)
    monkeypatch.setattr(yookassa_checkout, "normalize_gift_token", lambda token: (token or "").upper())
    monkeypatch.setattr(yookassa_checkout, "is_gift_token", lambda token: token.startswith("GIFT"))
    captured: list[Any] = []

    def urlopen(request: Any, timeout: int) -> Response:
        captured.append((request, timeout))
        return Response('{"confirmation":{"confirmation_url":"https://pay.example/ok"}}')

    monkeypatch.setattr(yookassa_checkout.urllib.request, "urlopen", urlopen)
    assert yookassa_checkout.create_yookassa_confirmation_url(
        source="max", external_user_id="external", user_id=77,
        kind="practice_package", package_id="pack", gift_token="gift_abc",
        checkout_intent="signed-body.signature",
    ) == "https://pay.example/ok"
    request, timeout = captured[0]
    assert timeout == 25
    payload = json.loads(request.data)
    assert payload["amount"]["value"] == "345.00"
    assert payload["metadata"]["external_user_id"] == "77"
    assert payload["metadata"]["messenger_external_user_id"] == "external"
    assert payload["metadata"]["kind"] == "tokens"
    assert payload["metadata"]["tokens"] == "12"
    assert payload["metadata"]["gift_token"] == "GIFT_ABC"
    headers = {key.casefold(): value for key, value in request.header_items()}
    assert headers["authorization"].startswith("Basic ")
    assert headers["idempotence-key"].startswith("metrotherapy:ci_")


def test_checkout_invalid_gift_legacy_and_provider_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    monkeypatch.setattr(yookassa_checkout, "normalize_gift_token", lambda token: token or "")
    monkeypatch.setattr(yookassa_checkout, "is_gift_token", lambda _token: False)
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="Invalid gift token"):
        yookassa_checkout.create_yookassa_confirmation_url(
            source="telegram", external_user_id="1", gift_token="bad"
        )

    monkeypatch.setattr(yookassa_checkout, "normalize_gift_token", lambda _token: "")
    monkeypatch.setattr(
        yookassa_checkout, "_legacy_amount_description",
        lambda kind: ("99.00", f"description:{kind}"),
    )
    monkeypatch.setattr(yookassa_checkout.uuid, "uuid4", lambda: SimpleNamespace(hex="abc"))
    captured: list[Any] = []
    monkeypatch.setattr(
        yookassa_checkout.urllib.request, "urlopen",
        lambda request, timeout: captured.append(request) or Response(
            '{"confirmation":{"url":"https://pay.example/legacy"}}'
        ),
    )
    assert yookassa_checkout.create_yookassa_confirmation_url(
        source="", external_user_id="5", kind="gift"
    ) == "https://pay.example/legacy"
    payload = json.loads(captured[0].data)
    assert payload["metadata"]["source"] == "unknown"
    assert payload["metadata"]["intent_id"] == "pi_abc"

    error = urllib.error.HTTPError(
        "https://api.yookassa.ru/v3/payments", 422, "bad", None,
        io.BytesIO(b'{"secret":"body"}'),
    )
    monkeypatch.setattr(
        yookassa_checkout.urllib.request, "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="HTTP 422"):
        yookassa_checkout.create_yookassa_confirmation_url(source="telegram", external_user_id="1")

    monkeypatch.setattr(
        yookassa_checkout.urllib.request, "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="network error"):
        yookassa_checkout.create_yookassa_confirmation_url(source="telegram", external_user_id="1")

    monkeypatch.setattr(
        yookassa_checkout.urllib.request, "urlopen",
        lambda *_args, **_kwargs: Response("{}"),
    )
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="confirmation_url"):
        yookassa_checkout.create_yookassa_confirmation_url(source="telegram", external_user_id="1")
