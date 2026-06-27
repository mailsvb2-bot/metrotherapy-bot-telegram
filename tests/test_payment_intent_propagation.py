from __future__ import annotations

from runtime import payment_http


def test_runtime_payment_passes_checkout_intent_to_provider(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_confirmation_url(**kwargs):
        captured.update(kwargs)
        return "https://payment.example/ok"

    monkeypatch.setattr(payment_http, "create_yookassa_confirmation_url", fake_create_confirmation_url)

    result = payment_http._create_yookassa_payment(
        source="telegram",
        external_user_id="123",
        kind="tokens",
        package_id="practice_start_7",
        gift_token=None,
        checkout_intent="signed-body.signature",
    )

    assert result == "https://payment.example/ok"
    assert captured["checkout_intent"] == "signed-body.signature"
    assert captured["user_id"] == "123"
    assert captured["external_user_id"] == "123"


def test_runtime_payment_keeps_canonical_user_id_separate_from_messenger_external_id(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_confirmation_url(**kwargs):
        captured.update(kwargs)
        return "https://payment.example/ok"

    monkeypatch.setattr(payment_http, "create_yookassa_confirmation_url", fake_create_confirmation_url)

    result = payment_http._create_yookassa_payment(
        source="max",
        user_id="902002",
        external_user_id="mx902002",
        kind="tokens",
        package_id="practice_start_7",
        gift_token="gift_" + "a" * 32,
        checkout_intent="signed-body.signature",
    )

    assert result == "https://payment.example/ok"
    assert captured["user_id"] == "902002"
    assert captured["external_user_id"] == "mx902002"
    assert captured["gift_token"] == "gift_" + "a" * 32
    assert captured["checkout_intent"] == "signed-body.signature"
