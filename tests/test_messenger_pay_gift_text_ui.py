from __future__ import annotations

from services.messenger.text_ui import handle_incoming_text
from services.schema import init_db


EXPECTED_PACKAGE_IDS = {
    "practice_start_7",
    "practice_60",
    "practice_antistress_60",
    "practice_personal_month",
}


def setup_module(module):
    init_db()


def _assert_canonical_package_payment_text(text: str, *, source: str) -> None:
    assert "💳 Тарифы Метротерапии" in text
    assert "/pay/yookassa" in text
    assert f"source={source}" in text
    assert "kind=tokens" in text
    for package_id in EXPECTED_PACKAGE_IDS:
        assert f"package_id={package_id}" in text
    assert "kind=subscription" not in text
    assert "kind=gift" not in text
    assert "morning_5" not in text
    assert "both_20" not in text


def _assert_canonical_gift_package_text(text: str, *, source: str) -> None:
    assert "🎁 Подарить Метротерапию" in text
    assert "/pay/yookassa" in text
    assert f"source={source}" in text
    assert "kind=tokens" in text
    for package_id in EXPECTED_PACKAGE_IDS:
        assert f"package_id={package_id}" in text
    assert "kind=gift" not in text
    assert "kind=subscription" not in text
    assert "morning_5" not in text
    assert "both_20" not in text


def test_vk_pay_command_returns_canonical_package_payment_text():
    user_id, replies = handle_incoming_text(
        902001,
        platform="vk",
        external_user_id="902001",
        text="💳 Тарифы",
    )

    assert user_id == 902001
    assert replies
    assert replies[0].kind == "text"
    _assert_canonical_package_payment_text(replies[0].text, source="vk")


def test_max_pay_command_returns_canonical_package_payment_text():
    user_id, replies = handle_incoming_text(
        902002,
        platform="max",
        external_user_id="mx902002",
        text="pay",
    )

    assert user_id == 902002
    assert replies
    assert replies[0].kind == "text"
    _assert_canonical_package_payment_text(replies[0].text, source="max")


def test_vk_gift_command_returns_canonical_package_gift_text():
    user_id, replies = handle_incoming_text(
        902003,
        platform="vk",
        external_user_id="902003",
        text="🎁 Подарить",
    )

    assert user_id == 902003
    assert replies
    assert replies[0].kind == "text"
    _assert_canonical_gift_package_text(replies[0].text, source="vk")


def test_max_gift_command_returns_canonical_package_gift_text():
    user_id, replies = handle_incoming_text(
        902004,
        platform="max",
        external_user_id="mx902004",
        text="gift",
    )

    assert user_id == 902004
    assert replies
    assert replies[0].kind == "text"
    _assert_canonical_gift_package_text(replies[0].text, source="max")
