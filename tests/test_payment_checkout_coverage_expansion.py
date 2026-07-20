from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.payments import yookassa_checkout
from services.validators import payment_contracts


def _clear_checkout_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APP_ENV",
        "ALLOW_STATIC_PAYMENT_IDEMPOTENCE_KEY_IN_PROD",
        "PAYMENT_IDEMPOTENCE_KEY",
        "YOOKASSA_IDEMPOTENCE_KEY",
        "YOOKASSA_RECEIPT_EMAIL",
        "PAYMENT_RECEIPT_EMAIL",
        "ADMIN_EMAIL",
        "YOOKASSA_TAX_SYSTEM_CODE",
        "YOOKASSA_VAT_CODE",
        "YOOKASSA_PAYMENT_MODE",
        "YOOKASSA_PAYMENT_SUBJECT",
        "PAYMENT_AMOUNT_RUB",
        "GIFT_PAYMENT_AMOUNT_RUB",
        "PAYMENT_DESCRIPTION",
        "GIFT_PAYMENT_DESCRIPTION",
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        "PAYMENT_RETURN_URL",
        "SITE_PUBLIC_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_checkout_environment_helpers_and_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_checkout_env(monkeypatch)
    assert yookassa_checkout._env_value("MISSING", " value ") == "value"
    assert yookassa_checkout._is_prod() is False
    assert yookassa_checkout._truthy_env("FLAG") is False
    assert yookassa_checkout._provider_error_body_for_log("one\ntwo") == "one two"
    assert yookassa_checkout._explicit_idempotence_key_allowed() is True

    monkeypatch.setenv("APP_ENV", "production")
    assert yookassa_checkout._is_prod() is True
    assert yookassa_checkout._provider_error_body_for_log("secret") == "<redacted in prod>"
    assert yookassa_checkout._explicit_idempotence_key_allowed() is False
    monkeypatch.setenv("ALLOW_STATIC_PAYMENT_IDEMPOTENCE_KEY_IN_PROD", "yes")
    assert yookassa_checkout._truthy_env("ALLOW_STATIC_PAYMENT_IDEMPOTENCE_KEY_IN_PROD") is True
    assert yookassa_checkout._explicit_idempotence_key_allowed() is True


def test_checkout_intent_and_idempotence_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_checkout_env(monkeypatch)
    assert yookassa_checkout._checkout_intent_id(None) is None
    assert yookassa_checkout._checkout_intent_id("  ") is None
    assert yookassa_checkout._checkout_intent_id(".signature") is None
    first = yookassa_checkout._checkout_intent_id("body.signature")
    second = yookassa_checkout._checkout_intent_id("body.other")
    assert first == second
    assert first is not None and first.startswith("ci_") and len(first) == 43

    monkeypatch.setenv("PAYMENT_IDEMPOTENCE_KEY", " x " * 100)
    explicit = yookassa_checkout._idempotence_key(
        source="telegram", external_user_id="1", kind="tokens", amount_value="10.00"
    )
    assert len(explicit) == 128

    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="forbidden in prod"):
        yookassa_checkout._idempotence_key(
            source="telegram", external_user_id="1", kind="tokens", amount_value="10.00"
        )

    monkeypatch.delenv("PAYMENT_IDEMPOTENCE_KEY")
    assert yookassa_checkout._idempotence_key(
        source="telegram",
        external_user_id="1",
        kind="tokens",
        amount_value="10.00",
        intent_id="ci_123",
    ) == "metrotherapy:ci_123"

    monkeypatch.setattr(yookassa_checkout.uuid, "uuid4", lambda: "random-key")
    assert yookassa_checkout._idempotence_key(
        source="telegram", external_user_id="1", kind="tokens", amount_value="10.00"
    ) == "random-key"


def test_receipt_email_integer_and_receipt_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_checkout_env(monkeypatch)
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
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="must be an integer"):
        yookassa_checkout._receipt_int("TEST_INT", 2, minimum=1, maximum=5)
    monkeypatch.setenv("TEST_INT", "9")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="between 1 and 5"):
        yookassa_checkout._receipt_int("TEST_INT", 2, minimum=1, maximum=5)

    _clear_checkout_env(monkeypatch)
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "buyer@example.test")
    monkeypatch.setattr(
        yookassa_checkout,
        "validate_receipt_contract",
        lambda **_kwargs: (2, 1, "full_payment", "service"),
    )
    receipt = yookassa_checkout.build_yookassa_receipt(
        amount_value="12.00", description="x" * 200
    )
    assert receipt["customer"] == {"email": "buyer@example.test"}
    assert receipt["tax_system_code"] == 2
    assert len(receipt["items"][0]["description"]) == 128
    assert receipt["items"][0]["amount"]["value"] == "12.00"

    def invalid_receipt(**_kwargs: Any) -> tuple[int, int, str, str]:
        raise ValueError("bad receipt")

    monkeypatch.setattr(yookassa_checkout, "validate_receipt_contract", invalid_receipt)
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="bad receipt"):
        yookassa_checkout.build_yookassa_receipt(amount_value="1.00", description="x")


def test_legacy_amount_description(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_checkout_env(monkeypatch)
    assert yookassa_checkout._legacy_amount_description("subscription") == (
        "990.00",
        "Metrotherapy access",
    )
    monkeypatch.setenv("PAYMENT_AMOUNT_RUB", "10,5")
    monkeypatch.setenv("PAYMENT_DESCRIPTION", "Access")
    assert yookassa_checkout._legacy_amount_description("subscription") == ("10.50", "Access")
    monkeypatch.setenv("GIFT_PAYMENT_AMOUNT_RUB", "20")
    monkeypatch.setenv("GIFT_PAYMENT_DESCRIPTION", "Gift")
    assert yookassa_checkout._legacy_amount_description("gift") == ("20.00", "Gift")
    monkeypatch.setenv("GIFT_PAYMENT_AMOUNT_RUB", "invalid")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="Invalid payment amount"):
        yookassa_checkout._legacy_amount_description("gift")


class _Response:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.payload.encode("utf-8")


def _configure_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_checkout_env(monkeypatch)
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setenv("YOOKASSA_RECEIPT_EMAIL", "buyer@example.test")
    monkeypatch.setattr(
        yookassa_checkout,
        "build_yookassa_receipt",
        lambda *, amount_value, description: {
            "amount": amount_value,
            "description": description,
        },
    )


def test_create_checkout_requires_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_checkout_env(monkeypatch)
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="SHOP_ID is empty"):
        yookassa_checkout.create_yookassa_confirmation_url(
            source="telegram", external_user_id="1"
        )
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop")
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="SECRET_KEY is empty"):
        yookassa_checkout.create_yookassa_confirmation_url(
            source="telegram", external_user_id="1"
        )


def test_create_token_and_gift_checkout_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_checkout(monkeypatch)
    captured: list[Any] = []
    package = SimpleNamespace(package_id="pack", tokens=12, price_rub=345, title="12 практик")
    monkeypatch.setattr(yookassa_checkout, "package_by_id", lambda package_id: package)
    monkeypatch.setattr(yookassa_checkout, "normalize_gift_token", lambda token: (token or "").upper())
    monkeypatch.setattr(yookassa_checkout, "is_gift_token", lambda token: token.startswith("GIFT"))

    def urlopen(request: Any, timeout: int) -> _Response:
        captured.append((request, timeout))
        return _Response('{"confirmation":{"confirmation_url":"https://pay.example/ok"}}')

    monkeypatch.setattr(yookassa_checkout.urllib.request, "urlopen", urlopen)
    url = yookassa_checkout.create_yookassa_confirmation_url(
        source="max",
        external_user_id="external",
        user_id=77,
        kind="practice_package",
        package_id="pack",
        gift_token="gift_abc",
        checkout_intent="signed-body.signature",
    )
    assert url == "https://pay.example/ok"
    request, timeout = captured[0]
    assert timeout == 25
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["amount"] == {"value": "345.00", "currency": "RUB"}
    assert payload["metadata"]["external_user_id"] == "77"
    assert payload["metadata"]["messenger_external_user_id"] == "external"
    assert payload["metadata"]["kind"] == "tokens"
    assert payload["metadata"]["package_id"] == "pack"
    assert payload["metadata"]["tokens"] == "12"
    assert payload["metadata"]["gift_token"] == "GIFT_ABC"
    headers = {key.casefold(): value for key, value in request.header_items()}
    assert headers["authorization"].startswith("Basic ")
    assert headers["idempotence-key"].startswith("metrotherapy:ci_")


def test_create_checkout_invalid_gift_and_legacy_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_checkout(monkeypatch)
    monkeypatch.setattr(yookassa_checkout, "normalize_gift_token", lambda token: token or "")
    monkeypatch.setattr(yookassa_checkout, "is_gift_token", lambda _token: False)
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="Invalid gift token"):
        yookassa_checkout.create_yookassa_confirmation_url(
            source="telegram", external_user_id="1", gift_token="bad"
        )

    monkeypatch.setattr(yookassa_checkout, "normalize_gift_token", lambda _token: "")
    monkeypatch.setattr(
        yookassa_checkout,
        "_legacy_amount_description",
        lambda kind: ("99.00", f"description:{kind}"),
    )
    monkeypatch.setattr(yookassa_checkout.uuid, "uuid4", lambda: SimpleNamespace(hex="abc"))
    captured: list[Any] = []

    def urlopen(request: Any, timeout: int) -> _Response:
        captured.append(request)
        return _Response('{"confirmation":{"url":"https://pay.example/legacy"}}')

    monkeypatch.setattr(yookassa_checkout.urllib.request, "urlopen", urlopen)
    assert yookassa_checkout.create_yookassa_confirmation_url(
        source="", external_user_id="5", kind="gift"
    ) == "https://pay.example/legacy"
    payload = json.loads(captured[0].data.decode("utf-8"))
    assert payload["metadata"]["source"] == "unknown"
    assert payload["metadata"]["intent_id"] == "pi_abc"
    assert payload["description"] == "description:gift"


def test_create_checkout_provider_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_checkout(monkeypatch)
    monkeypatch.setattr(yookassa_checkout, "normalize_gift_token", lambda _token: "")

    http_error = urllib.error.HTTPError(
        "https://api.yookassa.ru/v3/payments",
        422,
        "bad",
        hdrs=None,
        fp=io.BytesIO(b'{"secret":"provider-body"}'),
    )
    monkeypatch.setattr(
        yookassa_checkout.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(http_error),
    )
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="HTTP 422"):
        yookassa_checkout.create_yookassa_confirmation_url(
            source="telegram", external_user_id="1"
        )

    monkeypatch.setattr(
        yookassa_checkout.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="network error: offline"):
        yookassa_checkout.create_yookassa_confirmation_url(
            source="telegram", external_user_id="1"
        )

    monkeypatch.setattr(
        yookassa_checkout.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response("{}"),
    )
    with pytest.raises(yookassa_checkout.YooKassaCheckoutError, match="without confirmation_url"):
        yookassa_checkout.create_yookassa_confirmation_url(
            source="telegram", external_user_id="1"
        )


def test_payment_contract_helpers_and_validators(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path
    handlers = root / "handlers"
    payments = root / "services" / "payments"
    migrations = root / "services" / "migrations"
    excluded = root / ".venv"
    handlers.mkdir(parents=True)
    payments.mkdir(parents=True)
    migrations.mkdir(parents=True)
    excluded.mkdir()
    monkeypatch.setattr(payment_contracts, "PROJECT_ROOT", root)

    assert payment_contracts._read("missing.py") == ""
    handlers_file = handlers / "payments.py"
    handlers_file.write_text(
        "Legacy disabled _sub_pick_disabled _gift_buy_disabled sub:buy:",
        encoding="utf-8",
    )
    assert "Legacy" in payment_contracts._read("handlers/payments.py")
    assert payment_contracts._legacy_invoice_routes_are_disabled() is True

    good = payments / "good.py"
    good.write_text("price_rub = 10", encoding="utf-8")
    excluded_file = excluded / "ignored.py"
    excluded_file.write_text("PAY_PROVIDER_TOKEN", encoding="utf-8")
    assert good in payment_contracts._py_files()
    assert excluded_file not in payment_contracts._py_files()

    payment_contracts.validate_no_runtime_price_unit_heuristics(strict=True)
    bad_price = payments / "bad_price.py"
    bad_price.write_text(
        "if price_rub >= 50000:\n    price_rub = price_rub // 100\n",
        encoding="utf-8",
    )
    payment_contracts.validate_no_runtime_price_unit_heuristics(strict=False)
    with pytest.raises(payment_contracts.ValidationError, match="price-unit heuristic"):
        payment_contracts.validate_no_runtime_price_unit_heuristics(strict=True)

    migration = migrations / "price_rub_migration_v1.py"
    migration.write_text(bad_price.read_text(encoding="utf-8"), encoding="utf-8")
    bad_price.unlink()
    payment_contracts.validate_no_runtime_price_unit_heuristics(strict=True)

    payment_contracts.validate_legacy_invoice_routes_disabled(strict=True)
    handlers_file.write_text("sub:buy: active", encoding="utf-8")
    payment_contracts.validate_legacy_invoice_routes_disabled(strict=False)
    with pytest.raises(payment_contracts.ValidationError, match="still reachable"):
        payment_contracts.validate_legacy_invoice_routes_disabled(strict=True)

    handlers_file.write_text("no legacy route", encoding="utf-8")
    payment_contracts.validate_no_legacy_provider_token_in_payment_runtime(strict=True)
    legacy = payments / "legacy.py"
    legacy.write_text("PAY_PROVIDER_TOKEN", encoding="utf-8")
    payment_contracts.validate_no_legacy_provider_token_in_payment_runtime(strict=False)
    with pytest.raises(payment_contracts.ValidationError, match="forbidden"):
        payment_contracts.validate_no_legacy_provider_token_in_payment_runtime(strict=True)


def test_validate_payment_contracts_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        payment_contracts,
        "validate_legacy_invoice_routes_disabled",
        lambda *, strict: calls.append(("legacy", strict)),
    )
    monkeypatch.setattr(
        payment_contracts,
        "validate_no_runtime_price_unit_heuristics",
        lambda *, strict: calls.append(("price", strict)),
    )
    monkeypatch.setattr(
        payment_contracts,
        "validate_no_legacy_provider_token_in_payment_runtime",
        lambda *, strict: calls.append(("provider", strict)),
    )
    monkeypatch.setattr(
        payment_contracts,
        "validate_delivery_contracts",
        lambda *, strict: calls.append(("delivery", strict)),
    )
    payment_contracts.validate_payment_contracts(strict=False)
    assert calls == [
        ("legacy", False),
        ("price", False),
        ("provider", False),
        ("delivery", False),
    ]
