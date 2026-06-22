from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.payments import hooks


@pytest.fixture(autouse=True)
def _stub_plan_lookup(monkeypatch):
    def fake_get_plan_by_id(plan_id: int):
        if int(plan_id) == 10:
            return {
                "id": 10,
                "code": "both_5",
                "plan_code": "both_5",
                "title": "Plan",
                "scope": "both",
                "days": 5,
                "price": 990,
                "is_active": True,
            }
        if int(plan_id) == 20:
            return {
                "id": 20,
                "code": "inactive",
                "plan_code": "inactive",
                "title": "Inactive",
                "scope": "both",
                "days": 5,
                "price": 990,
                "is_active": False,
            }
        return None

    monkeypatch.setattr(hooks, "get_plan_by_id", fake_get_plan_by_id)


def test_pre_checkout_accepts_current_subscription_price():
    assert hooks.validate_pre_checkout_invoice(
        payload="sub:10",
        currency="RUB",
        total_amount=99000,
    ) is None


def test_pre_checkout_rejects_stale_subscription_price():
    error = hooks.validate_pre_checkout_invoice(
        payload="sub:10",
        currency="RUB",
        total_amount=79000,
    )

    assert error is not None


def test_pre_checkout_rejects_inactive_plan():
    error = hooks.validate_pre_checkout_invoice(
        payload="sub:20",
        currency="RUB",
        total_amount=99000,
    )

    assert error is not None


def test_pre_checkout_rejects_unknown_payload():
    error = hooks.validate_pre_checkout_invoice(
        payload="unknown:10",
        currency="RUB",
        total_amount=99000,
    )

    assert error is not None


@pytest.mark.asyncio
async def test_pre_checkout_answers_false_for_stale_price():
    calls: list[dict] = []

    class FakePreCheckout:
        from_user = SimpleNamespace(id=123)
        invoice_payload = "sub:10"
        currency = "RUB"
        total_amount = 79000

        async def answer(self, **kwargs):
            calls.append(kwargs)

    await hooks.pre_checkout(FakePreCheckout())

    assert len(calls) == 1
    assert calls[0]["ok"] is False
    assert calls[0]["error_message"]


@pytest.mark.asyncio
async def test_pre_checkout_answers_true_for_current_price():
    calls: list[dict] = []

    class FakePreCheckout:
        from_user = SimpleNamespace(id=123)
        invoice_payload = "sub:10|d=decision|c=correlation"
        currency = "RUB"
        total_amount = 99000

        async def answer(self, **kwargs):
            calls.append(kwargs)

    await hooks.pre_checkout(FakePreCheckout())

    assert calls == [{"ok": True}]
