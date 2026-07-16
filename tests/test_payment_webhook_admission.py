import pytest

from runtime import payment_webhook_admission as admission


def test_payment_webhook_rate_limit_is_bounded(monkeypatch):
    monkeypatch.setenv("PAYMENT_WEBHOOK_RATE_LIMIT", "2")
    monkeypatch.setenv("PAYMENT_WEBHOOK_RATE_WINDOW_SEC", "60")
    admission.reset_payment_webhook_admission_state_for_tests()

    assert admission._rate_allowed("client-a", now=100.0) is True
    assert admission._rate_allowed("client-a", now=101.0) is True
    assert admission._rate_allowed("client-a", now=102.0) is False
    assert admission._rate_allowed("client-b", now=102.0) is True
    assert admission._rate_allowed("client-a", now=161.1) is True


def test_payment_webhook_body_limit_is_strict_but_configurable(monkeypatch):
    monkeypatch.setenv("PAYMENT_WEBHOOK_MAX_BODY_BYTES", "8192")
    monkeypatch.setenv("HTTP_INGRESS_MAX_BODY_BYTES", "4096")
    assert admission.payment_webhook_body_limit() == 8192
    assert admission.ingress_body_limit() == 8192


@pytest.mark.asyncio
async def test_payment_webhook_verification_concurrency_is_bounded(monkeypatch):
    monkeypatch.setenv("PAYMENT_WEBHOOK_MAX_INFLIGHT", "1")
    monkeypatch.setenv("PAYMENT_WEBHOOK_QUEUE_TIMEOUT_MS", "1")
    admission.reset_payment_webhook_admission_state_for_tests()

    first = await admission._acquire_verification_slot()
    assert first is not None
    second = await admission._acquire_verification_slot()
    assert second is None
    first.release()
