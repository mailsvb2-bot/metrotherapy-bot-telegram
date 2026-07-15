from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.payments import stars_invoice_transport
from services.payments.telegram_stars import parse_stars_payload, send_stars_invoice
from services.practice_token_contract import telegram_stars_price


@pytest.mark.asyncio
async def test_runtime_stars_transport_uses_audited_invoice_link(monkeypatch) -> None:
    monkeypatch.setattr(stars_invoice_transport, "log_event", lambda *args, **kwargs: None)
    captured_link: dict = {}
    captured_answer: dict = {}

    class FakeBot:
        async def create_invoice_link(self, **kwargs):
            captured_link.update(kwargs)
            return "https://t.me/$metrotherapy-stars-test"

    class FakeMessage:
        from_user = SimpleNamespace(id=782001)
        bot = FakeBot()

        async def answer(self, text, **kwargs):
            captured_answer["text"] = text
            captured_answer.update(kwargs)

        async def answer_invoice(self, **_kwargs):
            raise AssertionError("production Stars transport must not use sendInvoice")

    token = await send_stars_invoice(
        FakeMessage(),  # type: ignore[arg-type]
        package_id="practice_start_7",
        as_gift=False,
    )

    assert token == ""
    assert captured_link["currency"] == "XTR"
    assert "provider_token" not in captured_link
    assert len(captured_link["prices"]) == 1
    assert captured_link["prices"][0].amount == telegram_stars_price("practice_start_7")
    assert parse_stars_payload(captured_link["payload"]).buyer_user_id == 782001

    markup = captured_answer["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert button.url == "https://t.me/$metrotherapy-stars-test"
    assert "Оплатить" in button.text
    assert "защищённую форму оплаты Telegram" in captured_answer["text"]


def test_package_installs_invoice_link_transport() -> None:
    assert send_stars_invoice is stars_invoice_transport.send_stars_invoice
