from __future__ import annotations

import pytest

from services.db import db
from services.messenger.preferences import record_channel_identity
from services.messenger.outbound import SenderRegistry
from services.premium_delivery import MemorySender, flush_premium_delivery_outbox
from services.premium_entitlements import (
    CONSULTATION_ENTITLEMENT,
    VIDEO_ENTITLEMENT,
    consultation_requests_summary,
    grant_premium_entitlements_for_payment,
    pending_delivery,
)


@pytest.mark.asyncio
async def test_antistress_package_grants_video_delivery_to_all_known_messengers(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'premium-video.db'))
    monkeypatch.setenv('STRESS_VIDEO_COURSE_URL', 'https://example.test/course')

    record_channel_identity(1001, 'telegram', '1001')
    record_channel_identity(1001, 'vk', 'vk-1001')
    record_channel_identity(1001, 'max', 'max-1001')

    result = grant_premium_entitlements_for_payment(
        user_id=1001,
        package_id='practice_antistress_60',
        provider='yookassa',
        provider_payment_id='pay-video-1',
    )

    assert result.video_granted is True
    assert result.consultation_granted is False
    assert result.outbox_created == 3
    assert len(pending_delivery()) == 3

    tg = MemorySender()
    vk = MemorySender()
    mx = MemorySender()
    flushed = await flush_premium_delivery_outbox(
        senders=SenderRegistry(telegram=tg, vk=vk, max=mx),
    )

    assert flushed.sent == 3
    assert 'https://example.test/course' in tg.messages[0][1]
    assert 'https://example.test/course' in vk.messages[0][1]
    assert 'https://example.test/course' in mx.messages[0][1]
    assert pending_delivery() == []


@pytest.mark.asyncio
async def test_personal_month_grants_video_and_consultation_request_idempotently(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'premium-personal.db'))

    record_channel_identity(2002, 'telegram', '2002')

    first = grant_premium_entitlements_for_payment(
        user_id=2002,
        package_id='practice_personal_month',
        provider='yookassa',
        provider_payment_id='pay-personal-1',
    )
    second = grant_premium_entitlements_for_payment(
        user_id=2002,
        package_id='practice_personal_month',
        provider='yookassa',
        provider_payment_id='pay-personal-1',
    )

    assert first.video_granted is True
    assert first.consultation_granted is True
    assert first.consultation_request_created is True
    assert first.outbox_created == 2
    assert second.video_granted is False
    assert second.consultation_granted is False
    assert second.consultation_request_created is False
    assert second.outbox_created == 0

    requests = consultation_requests_summary()
    assert len(requests) == 1
    assert requests[0]['user_id'] == 2002
    assert requests[0]['package_id'] == 'practice_personal_month'
    assert '60 минут' in requests[0]['contact_payload']

    with db() as conn:
        rows = conn.execute(
            'SELECT entitlement_type FROM premium_entitlements WHERE user_id=? ORDER BY entitlement_type',
            (2002,),
        ).fetchall()
    assert [row['entitlement_type'] for row in rows] == [CONSULTATION_ENTITLEMENT, VIDEO_ENTITLEMENT]
