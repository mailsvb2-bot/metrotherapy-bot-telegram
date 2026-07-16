import pytest

from runtime.messenger_senders import VkBotSender, provider_delivery_scope


@pytest.mark.asyncio
async def test_vk_random_ids_are_stable_per_durable_delivery(monkeypatch):
    calls: list[int] = []

    async def fake_vk_method(self, method, params):
        assert method == "messages.send"
        calls.append(int(params["random_id"]))
        return {"response": len(calls)}

    monkeypatch.setattr(VkBotSender, "_vk_method", fake_vk_method)

    with provider_delivery_scope("vk:event-100"):
        sender = VkBotSender(token="test")
        await sender.send_text("42", "one")
        await sender.send_text("42", "two")
    first_attempt = list(calls)

    calls.clear()
    with provider_delivery_scope("vk:event-100"):
        sender = VkBotSender(token="test")
        await sender.send_text("42", "one")
        await sender.send_text("42", "two")
    second_attempt = list(calls)

    assert first_attempt == second_attempt
    assert first_attempt[0] != first_attempt[1]
    assert all(0 < value <= 2_147_483_647 for value in first_attempt)


@pytest.mark.asyncio
async def test_different_outbox_events_get_different_vk_random_ids(monkeypatch):
    calls: list[int] = []

    async def fake_vk_method(self, method, params):
        calls.append(int(params["random_id"]))
        return {"response": 1}

    monkeypatch.setattr(VkBotSender, "_vk_method", fake_vk_method)

    with provider_delivery_scope("vk:event-a"):
        await VkBotSender(token="test").send_text("42", "same")
    with provider_delivery_scope("vk:event-b"):
        await VkBotSender(token="test").send_text("42", "same")

    assert calls[0] != calls[1]
