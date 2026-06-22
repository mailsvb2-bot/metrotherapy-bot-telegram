from __future__ import annotations

from services.payments.reconciliation import record_yookassa_webhook
from services.premium_entitlements import pending_delivery


def test_yookassa_source_metadata_controls_premium_delivery_fallback_platform():
    payload = {
        "event": "payment.succeeded",
        "object": {
            "id": "pay-vk-source-fallback-main-1",
            "status": "succeeded",
            "amount": {"value": "12900.00", "currency": "RUB"},
            "metadata": {
                "user_id": "90808",
                "external_user_id": "90808",
                "source": "vk",
                "kind": "tokens",
                "package_id": "practice_antistress_60",
            },
        },
    }

    result = record_yookassa_webhook(payload)

    assert result.problem == ""
    deliveries = pending_delivery(user_id=90808)
    assert len(deliveries) == 1
    assert deliveries[0]["platform"] == "vk"
    assert deliveries[0]["external_user_id"] == "90808"
    assert deliveries[0]["delivery_kind"] == "video_course_access"
