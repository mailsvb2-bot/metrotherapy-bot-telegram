from __future__ import annotations

from services.payments.reconciliation import ReconciliationResult
from services.payments import verified_reconciliation, yookassa_provider


def _non_grant_payload() -> dict:
    return {
        "event": "payment.canceled",
        "object": {
            "id": "payment_non_grant_1",
            "status": "canceled",
            "amount": {"value": "1900.00", "currency": "RUB"},
            "metadata": {
                "external_user_id": "123",
                "user_id": "123",
                "kind": "payment",
                "package_id": "",
                "gift_token": "",
            },
        },
    }


def test_non_grant_payment_webhook_is_verified_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", "1")
    provider_object = dict(_non_grant_payload()["object"])
    monkeypatch.setattr(yookassa_provider, "fetch_yookassa_payment", lambda payment_id: provider_object)

    verified = yookassa_provider.verify_yookassa_webhook_with_provider(_non_grant_payload())

    assert verified == provider_object


def test_reconciliation_persists_provider_canonical_status_not_webhook_status(monkeypatch):
    forged = _non_grant_payload()
    forged["event"] = "payment.succeeded"
    forged["object"]["status"] = "succeeded"

    provider_object = dict(forged["object"])
    provider_object["status"] = "pending"
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        verified_reconciliation,
        "verify_yookassa_webhook_with_provider",
        lambda payload: provider_object,
    )

    def fake_record(payload):
        captured["payload"] = payload
        return ReconciliationResult(
            ok=True,
            provider="yookassa",
            provider_payment_id="payment_non_grant_1",
            status=str(payload["object"]["status"]),
            event=str(payload["event"]),
            inserted=True,
            processing_status="provider_waiting",
            side_effects_done=False,
        )

    monkeypatch.setattr(verified_reconciliation, "record_yookassa_webhook", fake_record)

    result = verified_reconciliation.record_verified_yookassa_webhook(forged)

    canonical = captured["payload"]
    assert canonical["object"]["status"] == "pending"
    assert canonical["event"] == "payment.pending"
    assert result.status == "pending"
    assert result.side_effects_done is False
