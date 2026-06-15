from __future__ import annotations

import uuid

import pytest

from services.db import db
from services.messenger.preferences import record_channel_identity
from services.premium_entitlements import (
    CONSULTATION_ENTITLEMENT,
    VIDEO_ENTITLEMENT,
    consultation_requests_summary,
    grant_premium_entitlements_for_payment,
    pending_delivery,
)


def _unique_user_id() -> int:
    return 900_000_000 + int(uuid.uuid4().int % 10_000_000)


@pytest.mark.asyncio
async def test_antistress_package_grants_video_delivery_to_all_known_messengers(monkeypatch):
    monkeypatch.setenv("STRESS_VIDEO_COURSE_URL", "https://example.test/course")
    user_id = _unique_user_id()
    payment_id = f"pay-video-{uuid.uuid4().hex}"

    record_channel_identity(user_id, "telegram", str(user_id))
    record_channel_identity(user_id, "vk", f"vk-{user_id}")
    record_channel_identity(user_id, "max", f"max-{user_id}")

    result = grant_premium_entitlements_for_payment(
        user_id=user_id,
        package_id="practice_antistress_60",
        provider="yookassa",
        provider_payment_id=payment_id,
    )

    deliveries = pending_delivery(user_id=user_id)
    bodies = [str(item["body"]) for item in deliveries]
    platforms = {str(item["platform"]) for item in deliveries}

    assert result.video_granted is True
    assert result.consultation_granted is False
    assert result.outbox_created == 3
    assert len(deliveries) == 3
    assert platforms == {"telegram", "vk", "max"}
    assert all("https://example.test/course" in body for body in bodies)


@pytest.mark.asyncio
async def test_personal_month_grants_video_and_consultation_request_idempotently():
    user_id = _unique_user_id()
    payment_id = f"pay-personal-{uuid.uuid4().hex}"
    record_channel_identity(user_id, "telegram", str(user_id))

    first = grant_premium_entitlements_for_payment(
        user_id=user_id,
        package_id="practice_personal_month",
        provider="yookassa",
        provider_payment_id=payment_id,
    )
    second = grant_premium_entitlements_for_payment(
        user_id=user_id,
        package_id="practice_personal_month",
        provider="yookassa",
        provider_payment_id=payment_id,
    )

    assert first.video_granted is True
    assert first.consultation_granted is True
    assert first.consultation_request_created is True
    assert first.outbox_created == 2
    assert second.video_granted is False
    assert second.consultation_granted is False
    assert second.consultation_request_created is False
    assert second.outbox_created == 0

    requests = consultation_requests_summary(user_id=user_id)
    assert len(requests) == 1
    assert requests[0]["user_id"] == user_id
    assert requests[0]["package_id"] == "practice_personal_month"

    with db() as conn:
        rows = conn.execute(
            "SELECT entitlement_type FROM premium_entitlements WHERE user_id=? ORDER BY entitlement_type",
            (user_id,),
        ).fetchall()
    assert [row["entitlement_type"] for row in rows] == [CONSULTATION_ENTITLEMENT, VIDEO_ENTITLEMENT]
