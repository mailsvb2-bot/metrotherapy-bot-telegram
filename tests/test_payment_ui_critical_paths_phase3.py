from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from services.payments import ui
from services.practice_token_contract import PracticePackage


PUBLIC_A = PracticePackage("a", "Start", "First", 7, 2499, public=True, price_xtr=1500)
PUBLIC_B = PracticePackage("b", "Full", "Second", 60, 4199, public=True, price_xtr=2500)
PRIVATE = PracticePackage("private", "Hidden", "No", 1, 1, public=False, price_xtr=1)


def package_by_id(package_id: str) -> PracticePackage:
    return {"a": PUBLIC_A, "b": PUBLIC_B, "private": PRIVATE}[package_id]


def patch_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "package_by_id", package_by_id)
    monkeypatch.setattr(ui, "public_practice_packages", lambda: (PUBLIC_A, PUBLIC_B))
    monkeypatch.setattr(ui, "telegram_stars_price", lambda package_id: {"a": 1500, "b": 2500}[package_id])
    monkeypatch.setattr(ui, "stars_amount_label", lambda amount: f"{amount} Stars")


def buttons(markup: Any) -> list[Any]:
    return [button for row in markup.inline_keyboard for button in row]


def test_keyboard_helpers_and_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    button = ui.InlineKeyboardButton(text="Test", callback_data="test")
    assert ui.kb([[button]]).inline_keyboard == [[button]]
    back = ui.kb_back("target")
    assert back.inline_keyboard[0][0].callback_data == "target"
    assert ui._price_label(2499) == "2 499 ₽"
    monkeypatch.setattr(ui, "stars_amount_label", lambda amount: f"S{amount}")
    assert ui._stars_label(50) == "S50"


def test_practice_payment_url_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        ui,
        "payment_url",
        lambda base_url, **kwargs: calls.append((base_url, kwargs)) or "https://pay/raw",
    )
    monkeypatch.setattr(
        ui,
        "add_checkout_intent_to_url",
        lambda raw, **kwargs: calls.append((raw, kwargs)) or "https://pay/bound",
    )
    assert ui._practice_payment_url(
        base_url="https://pay",
        user_id=7,
        platform="vk",
        package_id="a",
        gift_token="gift_1",
    ) == "https://pay/bound"
    assert calls[0][1] == {
        "user_id": 7,
        "platform": "vk",
        "external_user_id": "7",
        "package_id": "a",
        "gift_token": "gift_1",
    }
    assert calls[1][1]["kind"] == "tokens"
    assert calls[1][1]["source"] == "vk"

    calls.clear()
    ui._practice_payment_url(
        base_url="https://pay", user_id=None, platform="max", package_id="b"
    )
    assert calls[0][1]["user_id"] == 0
    assert calls[0][1]["external_user_id"] is None


def test_telegram_package_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_packages(monkeypatch)
    rows = ui._telegram_package_rows(gift=False)
    assert [row[0].callback_data for row in rows] == ["pay:methods:a", "pay:methods:b"]
    assert rows[0][0].text == "📦 Start — 1500 Stars"
    gift_rows = ui._telegram_package_rows(gift=True)
    assert [row[0].callback_data for row in gift_rows] == ["pay:gift_methods:a", "pay:gift_methods:b"]


def test_telegram_payment_method_text(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_packages(monkeypatch)
    with pytest.raises(ValueError, match="payment_package_not_public"):
        ui.telegram_payment_method_text("private")

    monkeypatch.setattr(ui, "telegram_stars_enabled", lambda: False)
    unavailable = ui.telegram_payment_method_text("a")
    assert unavailable.startswith("Start\nFirst")
    assert "временно недоступна" in unavailable

    monkeypatch.setattr(ui, "telegram_stars_enabled", lambda: True)
    text = ui.telegram_payment_method_text("a")
    assert "Стоимость: 1500 Stars" in text
    assert "На этом экране ничего не списывается" in text
    assert "Stars уже есть" in text


def test_telegram_payment_methods_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_packages(monkeypatch)
    with pytest.raises(ValueError, match="payment_package_not_public"):
        ui.kb_telegram_payment_methods(user_id=7, package_id="private")
    with pytest.raises(ValueError, match="payment_buyer_required"):
        ui.kb_telegram_payment_methods(user_id=0, package_id="a")

    monkeypatch.setattr(ui, "telegram_stars_enabled", lambda: True)
    monkeypatch.setattr(
        ui,
        "stars_topup_url",
        lambda **kwargs: f"https://stars/{kwargs['package_id']}/{kwargs['amount_xtr']}",
    )
    personal = ui.kb_telegram_payment_methods(user_id=7, package_id="a")
    personal_buttons = buttons(personal)
    assert personal_buttons[0].callback_data == "stars:terms:a"
    assert personal_buttons[1].url == "https://stars/a/1500"
    assert personal_buttons[2].callback_data == "stars:terms:a"
    assert personal_buttons[-1].callback_data == "sub:menu"

    gift = ui.kb_telegram_payment_methods(user_id=7, package_id="a", gift=True)
    gift_buttons = buttons(gift)
    assert gift_buttons[0].callback_data == "stars:gift_terms:a"
    assert "подарок" in gift_buttons[0].text
    assert gift_buttons[-1].callback_data == "gift:menu"

    monkeypatch.setattr(ui, "telegram_stars_enabled", lambda: False)
    disabled = ui.kb_telegram_payment_methods(user_id=7, package_id="a")
    assert disabled.inline_keyboard[0][0].callback_data == "tariffs:stars_disabled"


def test_legacy_telegram_yookassa_is_blocked() -> None:
    with pytest.raises(ValueError, match="telegram_yookassa_disabled"):
        ui.kb_telegram_gift_yookassa_checkout(user_id=7, package_id="a")


def test_external_package_rows_missing_base(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_packages(monkeypatch)
    monkeypatch.setattr(ui, "payment_public_base_url", lambda: "")
    rows = ui._external_package_rows(user_id=7, platform="vk", gift=False)
    assert len(rows) == 2
    assert all(row[0].callback_data == "tariffs:public_base_missing" for row in rows)
    assert "2 499 ₽" in rows[0][0].text


def test_external_package_rows_personal_and_gift(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_packages(monkeypatch)
    monkeypatch.setattr(ui, "payment_public_base_url", lambda: "https://pay")
    urls: list[dict[str, Any]] = []
    gifts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ui,
        "_practice_payment_url",
        lambda **kwargs: urls.append(kwargs) or f"https://bound/{kwargs['package_id']}",
    )
    monkeypatch.setattr(
        ui,
        "create_gift_checkout_token",
        lambda **kwargs: gifts.append(kwargs) or f"gift_{kwargs['package_id']}",
    )

    personal = ui._external_package_rows(user_id=7, platform="vk", gift=False)
    assert personal[0][0].url == "https://bound/a"
    assert gifts == []
    assert urls[0]["gift_token"] is None

    urls.clear()
    gift = ui._external_package_rows(user_id=7, platform="max", gift=True)
    assert gift[1][0].url == "https://bound/b"
    assert gifts[0] == {"buyer_user_id": 7, "package_id": "a", "source_platform": "max"}
    assert urls[0]["gift_token"] == "gift_a"


def test_practice_package_route_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    warning = ui._practice_package_rows(user_id=None, platform="telegram", gift=True)
    assert warning[0][0].callback_data == "gift:menu"

    telegram_calls: list[bool] = []
    external_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ui,
        "_telegram_package_rows",
        lambda gift: telegram_calls.append(gift) or [[SimpleNamespace(callback_data="tg")]],
    )
    monkeypatch.setattr(
        ui,
        "_external_package_rows",
        lambda **kwargs: external_calls.append(kwargs) or [[SimpleNamespace(url="external")]],
    )
    assert ui._practice_package_rows(user_id=7, platform="telegram", gift=False)[0][0].callback_data == "tg"
    assert telegram_calls == [False]
    assert ui._practice_package_rows(user_id=7, platform="vk", gift=True)[0][0].url == "external"
    assert external_calls == [{"user_id": 7, "platform": "vk", "gift": True}]


def test_public_tariff_keyboards(monkeypatch: pytest.MonkeyPatch) -> None:
    package_button = ui.InlineKeyboardButton(text="Package", callback_data="package")
    monkeypatch.setattr(
        ui,
        "_practice_package_rows",
        lambda **_kwargs: [[package_button]],
    )
    tariff = ui.kb_tariffs(user_id=7)
    assert [row[0].callback_data for row in tariff.inline_keyboard] == [
        "package", "stars:terms", "gift:menu", "share:menu", "menu:main"
    ]
    gift = ui.kb_gift_tariffs(user_id=7, back_cb="custom")
    assert [row[0].callback_data for row in gift.inline_keyboard] == [
        "package", "stars:terms", "custom"
    ]

    selected = ui.kb_pay_selected()
    assert selected.inline_keyboard[0][0].callback_data == "pay:selected"
    after = ui.kb_after_paid()
    assert [row[0].callback_data for row in after.inline_keyboard] == [
        "settings:time:work", "settings:time:home", "menu:main"
    ]


def test_pick_user_keyboard_unavailable_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "ReplyKeyboardMarkup", None)
    monkeypatch.setattr(ui, "KeyboardButton", None)
    assert ui.pick_user_keyboard() is None

    class Button:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class Request:
        def __init__(self, request_id: int) -> None:
            self.request_id = request_id

    class Markup:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(ui, "ReplyKeyboardMarkup", Markup)
    monkeypatch.setattr(ui, "KeyboardButton", Button)
    monkeypatch.setattr(ui, "KeyboardButtonRequestUser", Request)
    markup = ui.pick_user_keyboard()
    assert len(markup.kwargs["keyboard"]) == 2
    assert markup.kwargs["keyboard"][0][0].kwargs["request_user"].request_id == 2

    monkeypatch.setattr(ui, "KeyboardButtonRequestUser", None)
    markup = ui.pick_user_keyboard()
    assert len(markup.kwargs["keyboard"]) == 1
    assert markup.kwargs["keyboard"][0][0].kwargs["text"] == "❌ Отмена"
