from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace
from typing import Any

import pytest

from services.payments import yookassa_provider as provider


def test_environment_flags_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", "0")
    assert provider._is_prod() is True
    assert provider.provider_verification_required() is True

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", raising=False)
    assert provider.provider_verification_required() is False
    monkeypatch.setenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", " yes ")
    assert provider.provider_verification_required() is True
    assert provider._truthy("ON") is True
    assert provider._truthy("no") is False

    monkeypatch.delenv("YOOKASSA_SHOP_ID", raising=False)
    monkeypatch.delenv("YOOKASSA_SECRET_KEY", raising=False)
    with pytest.raises(provider.YooKassaProviderVerificationError, match="missing_yookassa_credentials"):
        provider._auth_header()

    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "secret")
    assert provider._auth_header() == "Basic c2hvcDpzZWNyZXQ="


class Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_fetch_provider_object_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "_auth_header", lambda: "Basic test")
    requests: list[Any] = []

    def open_ok(req: Any, timeout: int) -> Response:
        requests.append((req, timeout))
        return Response(b'{"id":"p-1"}')

    monkeypatch.setattr(provider.urllib.request, "urlopen", open_ok)
    assert provider._fetch_provider_object("/payments/p-1", object_kind="payment") == {"id": "p-1"}
    assert requests[0][0].full_url.endswith("/v3/payments/p-1")
    assert requests[0][1] == 15

    error = urllib.error.HTTPError(
        "https://api.yookassa.ru", 403, "forbidden", {}, io.BytesIO(b'{"error":"secret"}')
    )
    monkeypatch.setattr(
        provider.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(provider.YooKassaProviderVerificationError, match="provider_http_403"):
        provider._fetch_provider_object("payments/p-1", object_kind="payment")

    monkeypatch.setattr(
        provider.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("network")),
    )
    with pytest.raises(provider.YooKassaProviderVerificationError, match="provider_network:OSError"):
        provider._fetch_provider_object("payments/p-1", object_kind="payment")

    monkeypatch.setattr(provider.urllib.request, "urlopen", lambda *_a, **_k: Response(b"bad"))
    with pytest.raises(provider.YooKassaProviderVerificationError, match="provider_bad_json"):
        provider._fetch_provider_object("payments/p-1", object_kind="payment")

    monkeypatch.setattr(provider.urllib.request, "urlopen", lambda *_a, **_k: Response(b"[]"))
    with pytest.raises(provider.YooKassaProviderVerificationError, match="provider_bad_payload"):
        provider._fetch_provider_object("payments/p-1", object_kind="payment")


def test_fetch_payment_and_refund_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        provider,
        "_fetch_provider_object",
        lambda path, object_kind: calls.append((path, object_kind)) or {"path": path},
    )
    with pytest.raises(provider.YooKassaProviderVerificationError, match="missing_provider_payment_id"):
        provider.fetch_yookassa_payment(" ")
    with pytest.raises(provider.YooKassaProviderVerificationError, match="missing_provider_refund_id"):
        provider.fetch_yookassa_refund("")
    assert provider.fetch_yookassa_payment(" p-1 ") == {"path": "payments/p-1"}
    assert provider.fetch_yookassa_refund(" r-1 ") == {"path": "refunds/r-1"}
    assert calls == [("payments/p-1", "payment"), ("refunds/r-1", "refund")]


def test_amount_and_payload_helpers() -> None:
    assert provider._minor(None) == 0
    assert provider._minor({"value": "10.005"}) == 1001
    assert provider._minor({"value": "10,004"}) == 1000
    assert provider._minor({"value": "bad"}) == 0
    assert provider._object({"object": {"id": "x"}}) == {"id": "x"}
    assert provider._object({"object": []}) == {}
    assert provider._amount({"amount": {"value": "1"}}) == {"value": "1"}
    assert provider._amount({"amount": "bad"}) == {}
    assert provider._metadata({"metadata": {"kind": "tokens"}}) == {"kind": "tokens"}
    assert provider._metadata({"metadata": None}) == {}

    grant = {
        "event": "payment.succeeded",
        "object": {"status": "succeeded", "metadata": {"kind": "tokens"}},
    }
    assert provider._grant_candidate(grant) is True
    package = {"object": {"status": "succeeded", "metadata": {"package_id": "pkg"}}}
    assert provider._grant_candidate(package) is True
    assert provider._grant_candidate({"object": {"status": "pending", "metadata": {"kind": "tokens"}}}) is False
    assert provider.webhook_requires_provider_verification({"object": {"id": "p-1"}}) is True
    assert provider.webhook_requires_provider_verification({}) is False


def payment_payload(**overrides: Any) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "id": "p-1",
        "status": "succeeded",
        "amount": {"value": "10.00", "currency": "RUB"},
        "metadata": {
            "external_user_id": "7",
            "user_id": "7",
            "kind": "tokens",
            "package_id": "pkg",
            "gift_token": "",
        },
    }
    obj.update(overrides.pop("object_overrides", {}))
    payload: dict[str, Any] = {"event": "payment.succeeded", "object": obj}
    payload.update(overrides)
    return payload


def test_verify_payment_disabled_and_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "provider_verification_required", lambda: False)
    assert provider.verify_yookassa_webhook_with_provider({}) is None

    monkeypatch.setattr(provider, "provider_verification_required", lambda: True)
    with pytest.raises(provider.YooKassaProviderVerificationError, match="missing_provider_payment_id"):
        provider.verify_yookassa_webhook_with_provider({})


def test_verify_payment_success_and_mismatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "provider_verification_required", lambda: True)
    payload = payment_payload()
    canonical = dict(payload["object"])
    monkeypatch.setattr(provider, "fetch_yookassa_payment", lambda _pid: canonical)
    assert provider.verify_yookassa_webhook_with_provider(payload) == canonical

    cases = [
        ({**canonical, "id": "other"}, "provider_id_mismatch"),
        ({**canonical, "status": "pending"}, "provider_status_not_succeeded"),
        ({**canonical, "amount": {"value": "11.00", "currency": "RUB"}}, "provider_amount_mismatch"),
        ({**canonical, "amount": {"value": "10.00", "currency": "USD"}}, "provider_currency_mismatch"),
    ]
    for remote, reason in cases:
        monkeypatch.setattr(provider, "fetch_yookassa_payment", lambda _pid, remote=remote: remote)
        with pytest.raises(provider.YooKassaProviderVerificationError, match=reason):
            provider.verify_yookassa_webhook_with_provider(payload)

    for key in ("external_user_id", "user_id", "kind", "package_id", "gift_token"):
        remote = dict(canonical)
        remote["metadata"] = dict(canonical["metadata"])
        remote["metadata"][key] = "different"
        monkeypatch.setattr(provider, "fetch_yookassa_payment", lambda _pid, remote=remote: remote)
        with pytest.raises(provider.YooKassaProviderVerificationError, match=f"provider_metadata_{key}_mismatch"):
            provider.verify_yookassa_webhook_with_provider(payload)


def refund_payload(**object_overrides: Any) -> dict[str, Any]:
    obj = {
        "id": "r-1",
        "payment_id": "p-1",
        "status": "succeeded",
        "amount": {"value": "5.00", "currency": "RUB"},
    }
    obj.update(object_overrides)
    return {"event": "refund.succeeded", "object": obj}


def test_verify_refund_disabled_and_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "provider_verification_required", lambda: False)
    assert provider.verify_yookassa_refund_webhook_with_provider({}) is None

    monkeypatch.setattr(provider, "provider_verification_required", lambda: True)
    with pytest.raises(provider.YooKassaProviderVerificationError, match="unexpected_refund_event"):
        provider.verify_yookassa_refund_webhook_with_provider({"event": "payment.succeeded"})
    with pytest.raises(provider.YooKassaProviderVerificationError, match="missing_provider_refund_id"):
        provider.verify_yookassa_refund_webhook_with_provider({"event": "refund.succeeded", "object": {}})
    with pytest.raises(provider.YooKassaProviderVerificationError, match="missing_refund_payment_id"):
        provider.verify_yookassa_refund_webhook_with_provider(
            {"event": "refund.succeeded", "object": {"id": "r-1"}}
        )


def test_verify_refund_success_and_mismatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "provider_verification_required", lambda: True)
    payload = refund_payload()
    canonical = dict(payload["object"])
    monkeypatch.setattr(provider, "fetch_yookassa_refund", lambda _rid: canonical)
    assert provider.verify_yookassa_refund_webhook_with_provider(payload) == canonical

    cases = [
        ({**canonical, "id": "other"}, "provider_refund_id_mismatch"),
        ({**canonical, "payment_id": "other"}, "provider_refund_payment_id_mismatch"),
        ({**canonical, "status": "pending"}, "provider_refund_not_succeeded"),
        ({**canonical, "amount": {"value": "4.00", "currency": "RUB"}}, "provider_refund_amount_mismatch"),
        ({**canonical, "amount": {"value": "5.00", "currency": "USD"}}, "provider_refund_currency_mismatch"),
    ]
    for remote, reason in cases:
        monkeypatch.setattr(provider, "fetch_yookassa_refund", lambda _rid, remote=remote: remote)
        with pytest.raises(provider.YooKassaProviderVerificationError, match=reason):
            provider.verify_yookassa_refund_webhook_with_provider(payload)

    zero_payload = refund_payload(amount={"value": "0", "currency": "RUB"})
    monkeypatch.setattr(provider, "fetch_yookassa_refund", lambda _rid: dict(zero_payload["object"]))
    with pytest.raises(provider.YooKassaProviderVerificationError, match="provider_refund_amount_mismatch"):
        provider.verify_yookassa_refund_webhook_with_provider(zero_payload)
