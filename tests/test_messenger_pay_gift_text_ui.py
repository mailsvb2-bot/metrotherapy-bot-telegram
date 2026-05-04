from __future__ import annotations

from services.messenger.text_ui import handle_incoming_text
from services.schema import init_db


def setup_module(module):
    init_db()


def test_vk_pay_command_returns_payment_link_text():
    user_id, replies = handle_incoming_text(
        902001,
        platform="vk",
        external_user_id="902001",
        text="💳 Тарифы",
    )

    assert user_id == 902001
    assert replies
    assert replies[0].kind == "text"
    assert "Оплата" in replies[0].text
    assert "/pay/yookassa" in replies[0].text
    assert "source=vk" in replies[0].text


def test_max_pay_command_returns_payment_link_text():
    user_id, replies = handle_incoming_text(
        902002,
        platform="max",
        external_user_id="mx902002",
        text="pay",
    )

    assert user_id == 902002
    assert replies
    assert replies[0].kind == "text"
    assert "Оплата" in replies[0].text
    assert "/pay/yookassa" in replies[0].text
    assert "source=max" in replies[0].text


def test_vk_gift_command_returns_gift_payment_link_text():
    user_id, replies = handle_incoming_text(
        902003,
        platform="vk",
        external_user_id="902003",
        text="🎁 Подарить",
    )

    assert user_id == 902003
    assert replies
    assert replies[0].kind == "text"
    assert "Подарить" in replies[0].text
    assert "/pay/yookassa" in replies[0].text
    assert "kind=gift" in replies[0].text


def test_max_gift_command_returns_gift_payment_link_text():
    user_id, replies = handle_incoming_text(
        902004,
        platform="max",
        external_user_id="mx902004",
        text="gift",
    )

    assert user_id == 902004
    assert replies
    assert replies[0].kind == "text"
    assert "Подарить" in replies[0].text
    assert "/pay/yookassa" in replies[0].text
    assert "kind=gift" in replies[0].text
