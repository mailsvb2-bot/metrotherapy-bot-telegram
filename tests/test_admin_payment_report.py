from __future__ import annotations

from services.admin_payment_report import build_admin_payment_report, render_admin_payment_report_text
from services.messenger.preferences import record_channel_identity
from services.payments.reconciliation import record_yookassa_webhook


def _succeeded_payment(payment_id: str, *, user_id: int, package_id: str, amount: str, source: str = "telegram") -> dict:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": amount, "currency": "RUB"},
            "metadata": {
                "project": "metrotherapy",
                "user_id": str(user_id),
                "external_user_id": str(user_id),
                "source": source,
                "kind": "tokens",
                "package_id": package_id,
            },
        },
    }


def test_admin_payment_report_shows_payment_problem_and_consultation_request():
    user_id = 7001
    record_channel_identity(user_id, "vk", "vk-7001")

    problem = record_yookassa_webhook(
        _succeeded_payment(
            "pay-problem-admin-1",
            user_id=user_id,
            package_id="practice_60",
            amount="1.00",
            source="vk",
        )
    )
    assert problem.problem == "amount_mismatch_for_practice_grant"

    personal = record_yookassa_webhook(
        _succeeded_payment(
            "pay-personal-admin-1",
            user_id=user_id,
            package_id="practice_personal_month",
            amount="24870.00",
            source="vk",
        )
    )
    assert personal.ok is True
    assert personal.problem == ""

    report = build_admin_payment_report(limit=20, user_id=user_id)

    assert report.ok is True
    assert report.payment_problem_count == 1
    assert report.payment_problems[0]["provider_charge_id"] == "pay-problem-admin-1"
    assert report.payment_problems[0]["user_id"] == user_id
    assert report.payment_problems[0]["provider_status"] == "succeeded"
    assert report.payment_problems[0]["problem"] == "amount_mismatch_for_practice_grant"

    assert report.consultation_request_count == 1
    request = report.consultation_requests[0]
    assert request["user_id"] == user_id
    assert request["platform"] == "vk"
    assert request["external_user_id"] == "vk-7001"
    assert request["package_id"] == "practice_personal_month"
    assert request["provider_payment_id"] == "pay-personal-admin-1"
    assert request["status"] == "new"

    text = render_admin_payment_report_text(report)
    assert "Admin payment report" in text
    assert "Payment problems: 1" in text
    assert "payment_id=pay-problem-admin-1" in text
    assert "problem=amount_mismatch_for_practice_grant" in text
    assert "Consultation requests: 1" in text
    assert "platform=vk" in text
    assert "external_user_id=vk-7001" in text
    assert "package_id=practice_personal_month" in text
    assert "payment_id=pay-personal-admin-1" in text


def test_admin_payment_report_empty_state_for_unknown_user():
    report = build_admin_payment_report(limit=20, user_id=987654321)
    text = render_admin_payment_report_text(report)

    assert report.payment_problem_count == 0
    assert report.consultation_request_count == 0
    assert "Payment problems: 0" in text
    assert "Consultation requests: 0" in text
    assert "no records requiring attention" in text
    assert "no new requests" in text
