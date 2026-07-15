from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.payments import stars_invoice_transport
from services.payments.telegram_stars import parse_stars_payload, send_stars_invoice
from services.practice_token_contract import telegram_stars_price


TOPUP_URL = "tg://stars_topup?balance=1226&purpose=metrotherapy_practice_start_7"


def test_stars_topup_url_targets_exact_package_amount() -> None:
    assert (
        stars_invoice_transport._stars_topup_url(
            amount_xtr=1226,
            package_id="practice_start_7",
        )
        == TOPUP_URL
    )
    with pytest.raises(ValueError, match="stars_topup_amount_invalid"):
        stars_invoice_transport._stars_topup_url(amount_xtr=0, package_id="practice_start_7")


@pytest.mark.asyncio
async def test_runtime_stars_transport_uses_audited_invoice_link_with_recovery(monkeypatch) -> None:
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
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert buttons[0].url == "https://t.me/$metrotherapy-stars-test"
    assert buttons[0].text == "⭐ Оплатить пакет — 1 226 Stars"
    assert buttons[1].url == TOPUP_URL
    assert buttons[1].text == "➕ Купить 1 226 Stars"
    assert buttons[2].callback_data == "stars:buy:practice_start_7"
    assert buttons[2].text == "🔄 Stars куплены — продолжить оплату"
    assert buttons[3].callback_data == "pay:methods:practice_start_7"
    assert "Telegram откроет штатное окно пополнения" in captured_answer["text"]
    assert "Настройки → Ваши Stars" in captured_answer["text"]
    assert "Метротерапия не получает и не хранит данные вашей карты" in captured_answer["text"]
    assert "PremiumBot" not in captured_answer["text"]


@pytest.mark.asyncio
async def test_gift_recovery_preserves_gift_callbacks(monkeypatch) -> None:
    gift_token = "gift_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    monkeypatch.setattr(stars_invoice_transport, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        stars_invoice_transport,
        "create_gift_checkout_token",
        lambda **_kwargs: gift_token,
    )
    captured_answer: dict = {}

    class FakeBot:
        async def create_invoice_link(self, **_kwargs):
            return "https://t.me/$metrotherapy-stars-gift-test"

    class FakeMessage:
        from_user = SimpleNamespace(id=782002)
        bot = FakeBot()

        async def answer(self, text, **kwargs):
            captured_answer["text"] = text
            captured_answer.update(kwargs)

        async def answer_invoice(self, **_kwargs):
            raise AssertionError("production Stars transport must not use sendInvoice")

    token = await send_stars_invoice(
        FakeMessage(),  # type: ignore[arg-type]
        package_id="practice_start_7",
        as_gift=True,
    )

    assert token == gift_token
    buttons = [button for row in captured_answer["reply_markup"].inline_keyboard for button in row]
    assert buttons[1].url == TOPUP_URL
    assert buttons[2].callback_data == "stars:gift:practice_start_7"
    assert buttons[3].callback_data == "pay:gift_methods:practice_start_7"
    assert all("PremiumBot" not in str(button.url or "") for button in buttons)


def test_package_installs_invoice_link_transport() -> None:
    assert send_stars_invoice is stars_invoice_transport.send_stars_invoice
