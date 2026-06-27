from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from services.messenger.text_ui import handle_incoming_text
from services.payments.checkout_intent import verify_checkout_intent
from services.schema import init_db


EXPECTED_PACKAGE_IDS = {
    "practice_start_7",
    "practice_60",
    "practice_antistress_60",
    "practice_personal_month",
}


def setup_module(module):
    init_db()


def _payment_urls(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip().startswith("http") and "/pay/yookassa" in line]


def _query(url: str) -> dict[str, str]:
    values = parse_qs(urlsplit(url).query, keep_blank_values=True)
    return {key: item[-1] for key, item in values.items() if item}


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


def test_max_payment_link_uses_canonical_user_id_and_preserves_external_id():
    user_id, replies = handle_incoming_text(
        902002,
        platform="max",
        external_user_id="mx902002",
        text="pay",
    )

    assert user_id == 902002
    url = _payment_urls(replies[0].text)[0]
    query = _query(url)
    assert query["user_id"] == "902002"
    assert query["external_user_id"] == "mx902002"
    assert query["source"] == "max"
    assert query["kind"] == "tokens"
    assert query["package_id"] in EXPECTED_PACKAGE_IDS
    verify_checkout_intent(
        query["intent"],
        expected_user_id="902002",
        expected_package_id=query["package_id"],
        expected_kind="tokens",
    )


def test_max_gift_link_uses_reserved_gift_token_and_canonical_intent():
    user_id, replies = handle_incoming_text(
        902004,
        platform="max",
        external_user_id="mx902004",
        text="gift",
    )

    assert user_id == 902004
    url = _payment_urls(replies[0].text)[0]
    query = _query(url)
    assert query["user_id"] == "902004"
    assert query["external_user_id"] == "mx902004"
    assert query["source"] == "max"
    assert query["kind"] == "tokens"
    assert query["gift"] == "1"
    assert query["gift_token"].startswith("gift_")
    verify_checkout_intent(
        query["intent"],
        expected_user_id="902004",
        expected_package_id=query["package_id"],
        expected_kind="tokens",
        expected_gift_token=query["gift_token"],
    )
