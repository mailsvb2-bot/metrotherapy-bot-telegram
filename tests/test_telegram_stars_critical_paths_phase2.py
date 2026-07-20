from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from services.payments import telegram_stars as stars
from services.practice_token_contract import PracticePackage


PUBLIC = PracticePackage("pkg", "Package", "Description", 7, 100, public=True, price_xtr=50)
PRIVATE = PracticePackage("private", "Private", "Hidden", 7, 100, public=False, price_xtr=50)


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


@contextmanager
def no_tx(conn: Any):
    yield conn


class Cursor:
    def __init__(self, row: Any = None, rowcount: int = 0) -> None:
        self._row = row
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._row


class Conn:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = list(rows or [])
        self.calls: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = None) -> Cursor:
        self.calls.append((" ".join(query.split()), params))
        row = self.rows.pop(0) if self.rows else None
        return Cursor(row=row, rowcount=1)


def patch_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    def by_id(package_id: str) -> PracticePackage:
        if package_id == "private":
            return PRIVATE
        if package_id == "pkg":
            return PUBLIC
        raise ValueError("unknown")

    monkeypatch.setattr(stars, "package_by_id", by_id)
    monkeypatch.setattr(stars, "telegram_stars_price", lambda package_id: 50)
    monkeypatch.setattr(stars, "normalize_gift_token", lambda token: str(token or "").strip())
    monkeypatch.setattr(stars, "is_gift_token", lambda token: token.startswith("gift_"))


def test_build_payload_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_packages(monkeypatch)
    with pytest.raises(ValueError, match="buyer_user_id_required"):
        stars.build_stars_payload(buyer_user_id=0, package_id="pkg")
    with pytest.raises(ValueError, match="package_not_public"):
        stars.build_stars_payload(buyer_user_id=1, package_id="private")
    with pytest.raises(ValueError, match="gift_token_invalid"):
        stars.build_stars_payload(buyer_user_id=1, package_id="pkg", gift_token="bad")

    assert stars.build_stars_payload(buyer_user_id=7, package_id="pkg") == "xtr:v1:p:7:pkg:50"
    assert stars.build_stars_payload(
        buyer_user_id=7, package_id="pkg", gift_token="gift_abc"
    ) == "xtr:v1:g:7:pkg:50:gift_abc"

    monkeypatch.setattr(stars, "normalize_gift_token", lambda _token: "gift_" + "x" * 200)
    with pytest.raises(ValueError, match="payload_too_long"):
        stars.build_stars_payload(buyer_user_id=1, package_id="pkg", gift_token="gift_x")


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("", "payload_invalid"),
        ("xtr:v1:z:7:pkg:50", "kind_invalid"),
        ("xtr:v1:p:x:pkg:50", "numeric_field_invalid"),
        ("xtr:v1:p:0:pkg:50", "buyer_user_id_invalid"),
        ("xtr:v1:p:7:pkg:0", "amount_invalid"),
        ("xtr:v1:p:7:pkg:100001", "amount_invalid"),
        ("xtr:v1:p:7:private:50", "package_not_public"),
        ("xtr:v1:g:7:pkg:50:bad", "gift_token_invalid"),
        ("xtr:v1:p:7:pkg:50:gift_abc", "payload_invalid"),
    ],
)
def test_parse_payload_rejections(
    monkeypatch: pytest.MonkeyPatch, payload: str, reason: str
) -> None:
    patch_packages(monkeypatch)
    with pytest.raises(ValueError, match=reason):
        stars.parse_stars_payload(payload)


def test_parse_payload_success(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_packages(monkeypatch)
    personal = stars.parse_stars_payload("xtr:v1:p:7:pkg:50")
    assert personal == stars.StarsOrder(7, "pkg", 50, "")
    assert personal.is_gift is False
    gift = stars.parse_stars_payload("xtr:v1:g:7:pkg:50:gift_abc")
    assert gift.is_gift is True


@pytest.mark.parametrize(
    ("row", "pre_checkout", "problem"),
    [
        (None, True, "stars_gift_not_found"),
        ((8, "pkg", "created"), True, "stars_gift_buyer_mismatch"),
        ((7, "other", "created"), True, "stars_gift_package_mismatch"),
        ((7, "pkg", "paid"), True, "stars_gift_not_payable"),
        ((7, "pkg", "claimed"), False, ""),
    ],
)
def test_gift_claim_problem(
    monkeypatch: pytest.MonkeyPatch,
    row: Any,
    pre_checkout: bool,
    problem: str,
) -> None:
    conn = Conn([row])
    monkeypatch.setattr(stars, "db", lambda: DbContext(conn))
    order = stars.StarsOrder(7, "pkg", 50, "gift_abc")
    assert stars._gift_claim_problem(order, pre_checkout=pre_checkout) == problem
    assert stars._gift_claim_problem(stars.StarsOrder(7, "pkg", 50), pre_checkout=True) == ""


def test_validate_stars_order_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_packages(monkeypatch)
    monkeypatch.setattr(stars, "telegram_stars_enabled", lambda: False)
    with pytest.raises(ValueError, match="payments_disabled"):
        stars.validate_stars_order(
            payload="xtr:v1:p:7:pkg:50", user_id=7, currency="XTR", total_amount=50, pre_checkout=True
        )

    monkeypatch.setattr(stars, "telegram_stars_enabled", lambda: True)
    with pytest.raises(ValueError, match="currency_invalid"):
        stars.validate_stars_order(
            payload="xtr:v1:p:7:pkg:50", user_id=7, currency="RUB", total_amount=50, pre_checkout=True
        )
    with pytest.raises(ValueError, match="buyer_mismatch"):
        stars.validate_stars_order(
            payload="xtr:v1:p:7:pkg:50", user_id=8, currency="XTR", total_amount=50, pre_checkout=True
        )
    with pytest.raises(ValueError, match="amount_mismatch"):
        stars.validate_stars_order(
            payload="xtr:v1:p:7:pkg:50", user_id=7, currency="XTR", total_amount=51, pre_checkout=True
        )

    monkeypatch.setattr(stars, "telegram_stars_price", lambda _package_id: 55)
    with pytest.raises(ValueError, match="price_stale"):
        stars.validate_stars_order(
            payload="xtr:v1:p:7:pkg:50", user_id=7, currency="XTR", total_amount=50, pre_checkout=True
        )

    monkeypatch.setattr(stars, "telegram_stars_price", lambda _package_id: 50)
    monkeypatch.setattr(stars, "_gift_claim_problem", lambda _order, pre_checkout: "gift_problem")
    with pytest.raises(ValueError, match="gift_problem"):
        stars.validate_stars_order(
            payload="xtr:v1:p:7:pkg:50", user_id=7, currency="XTR", total_amount=50, pre_checkout=False
        )

    monkeypatch.setattr(stars, "_gift_claim_problem", lambda _order, pre_checkout: "")
    order = stars.validate_stars_order(
        payload="xtr:v1:p:7:pkg:50", user_id=7, currency="XTR", total_amount=50, pre_checkout=False
    )
    assert order.package_id == "pkg"


def test_pre_checkout_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        stars,
        "validate_stars_order",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad")),
    )
    assert stars.validate_stars_pre_checkout(
        payload="p", user_id=7, currency="XTR", total_amount=50
    ) is not None
    monkeypatch.setattr(stars, "validate_stars_order", lambda **_kwargs: stars.StarsOrder(7, "pkg", 50))
    assert stars.validate_stars_pre_checkout(
        payload="p", user_id=7, currency="XTR", total_amount=50
    ) is None


class InvoiceMessage:
    def __init__(self, user: Any) -> None:
        self.from_user = user
        self.calls: list[dict[str, Any]] = []

    async def answer_invoice(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_send_stars_invoice_guards_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_packages(monkeypatch)
    monkeypatch.setattr(stars, "telegram_stars_enabled", lambda: False)
    with pytest.raises(stars.StarsPaymentError, match="payments_disabled"):
        await stars.send_stars_invoice(InvoiceMessage(SimpleNamespace(id=7)), package_id="pkg")

    monkeypatch.setattr(stars, "telegram_stars_enabled", lambda: True)
    with pytest.raises(stars.StarsPaymentError, match="buyer_missing"):
        await stars.send_stars_invoice(InvoiceMessage(None), package_id="pkg")
    with pytest.raises(stars.StarsPaymentError, match="package_not_public"):
        await stars.send_stars_invoice(InvoiceMessage(SimpleNamespace(id=7)), package_id="private")

    events: list[Any] = []
    monkeypatch.setattr(stars, "log_event", lambda *args: events.append(args))
    monkeypatch.setattr(stars, "create_gift_checkout_token", lambda **_kwargs: "gift_abc")
    message = InvoiceMessage(SimpleNamespace(id=7))
    token = await stars.send_stars_invoice(message, package_id="pkg", as_gift=True)
    assert token == "gift_abc"
    assert message.calls[0]["currency"] == "XTR"
    assert message.calls[0]["prices"][0].amount == 50
    assert "Подарок" in message.calls[0]["description"]
    assert events[0][1] == "telegram_stars_invoice_created"


def test_payment_row_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    for row, expected in [
        (None, {}),
        ({"user_id": 7}, {"user_id": 7}),
        ((7, "charge", "payload", 50, "XTR", "pending", None), {
            "user_id": 7,
            "telegram_charge_id": "charge",
            "payload": "payload",
            "amount": 50,
            "currency": "XTR",
            "processing_status": "pending",
            "side_effects_done_at_utc": None,
        }),
    ]:
        monkeypatch.setattr(stars, "db", lambda row=row: DbContext(Conn([row])))
        assert stars._payment_row("charge") == expected


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({}, "fact_missing"),
        ({"user_id": 8, "payload": "p", "amount": 50, "currency": "XTR"}, "user_conflict"),
        ({"user_id": 7, "payload": "other", "amount": 50, "currency": "XTR"}, "payload_conflict"),
        ({"user_id": 7, "payload": "p", "amount": 51, "currency": "XTR"}, "amount_conflict"),
        ({"user_id": 7, "payload": "p", "amount": 50, "currency": "RUB"}, "currency_conflict"),
    ],
)
def test_record_received_payment_conflicts(
    monkeypatch: pytest.MonkeyPatch, row: dict[str, Any], reason: str
) -> None:
    conn = Conn()
    monkeypatch.setattr(stars, "db", lambda: DbContext(conn))
    monkeypatch.setattr(stars, "tx", no_tx)
    monkeypatch.setattr(stars, "_utc_iso", lambda: "NOW")
    monkeypatch.setattr(stars, "_payment_row", lambda _charge: row)
    with pytest.raises(stars.StarsPaymentError, match=reason):
        stars._record_received_payment_fact(
            user_id=7,
            charge_id="charge",
            provider_charge_id="provider",
            payload="p",
            amount=50,
            currency="xtr",
        )


def test_record_received_validated_done_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = Conn()
    monkeypatch.setattr(stars, "db", lambda: DbContext(conn))
    monkeypatch.setattr(stars, "tx", no_tx)
    monkeypatch.setattr(stars, "_utc_iso", lambda: "NOW")
    valid = {"user_id": 7, "payload": "p", "amount": 50, "currency": "XTR"}
    monkeypatch.setattr(stars, "_payment_row", lambda _charge: valid)

    stars._record_received_payment_fact(
        user_id=7, charge_id="charge", provider_charge_id="", payload="p", amount=50, currency="xtr"
    )
    stars._record_validated_payment_fact(
        order=stars.StarsOrder(7, "pkg", 50),
        charge_id="charge",
        provider_charge_id="provider",
        payload="p",
        amount=50,
    )
    stars._mark_payment_done("charge")
    stars._mark_payment_error("charge", ValueError("safe_code"))
    assert any("INSERT OR IGNORE INTO payments" in query for query, _ in conn.calls)
    assert any("side_effects_done" in query for query, _ in conn.calls)
    assert conn.calls[-1][1] == ("ValueError:safe_code", "ValueError:safe_code", "charge")


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({}, "fact_missing"),
        ({"user_id": 8, "payload": "p", "amount": 50, "currency": "XTR"}, "user_conflict"),
        ({"user_id": 7, "payload": "other", "amount": 50, "currency": "XTR"}, "payload_conflict"),
        ({"user_id": 7, "payload": "p", "amount": 51, "currency": "XTR"}, "amount_conflict"),
        ({"user_id": 7, "payload": "p", "amount": 50, "currency": "RUB"}, "currency_conflict"),
    ],
)
def test_record_validated_payment_conflicts(
    monkeypatch: pytest.MonkeyPatch, row: dict[str, Any], reason: str
) -> None:
    monkeypatch.setattr(stars, "db", lambda: DbContext(Conn()))
    monkeypatch.setattr(stars, "tx", no_tx)
    monkeypatch.setattr(stars, "_utc_iso", lambda: "NOW")
    monkeypatch.setattr(stars, "_payment_row", lambda _charge: row)
    with pytest.raises(stars.StarsPaymentError, match=reason):
        stars._record_validated_payment_fact(
            order=stars.StarsOrder(7, "pkg", 50),
            charge_id="charge",
            provider_charge_id="provider",
            payload="p",
            amount=50,
        )


def patch_success_pipeline(monkeypatch: pytest.MonkeyPatch, *, gift: bool = False) -> None:
    order = stars.StarsOrder(7, "pkg", 50, "gift_abc" if gift else "")
    monkeypatch.setattr(stars, "_record_received_payment_fact", lambda **_kwargs: None)
    monkeypatch.setattr(stars, "validate_stars_order", lambda **_kwargs: order)
    monkeypatch.setattr(stars, "_record_validated_payment_fact", lambda **_kwargs: None)


def test_record_successful_stars_payment_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(stars.StarsPaymentError, match="charge_id_missing"):
        stars.record_successful_stars_payment(
            user_id=7, payload="p", total_amount=50, currency="XTR", telegram_charge_id=""
        )
    with pytest.raises(stars.StarsPaymentError, match="amount_invalid"):
        stars.record_successful_stars_payment(
            user_id=7, payload="p", total_amount="bad", currency="XTR", telegram_charge_id="charge"
        )

    error_calls: list[Any] = []
    monkeypatch.setattr(stars, "_record_received_payment_fact", lambda **_kwargs: None)
    monkeypatch.setattr(
        stars,
        "validate_stars_order",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("validation")),
    )
    monkeypatch.setattr(stars, "_mark_payment_error", lambda *args: error_calls.append(args))
    with pytest.raises(stars.StarsPaymentError, match="validation_failed"):
        stars.record_successful_stars_payment(
            user_id=7, payload="p", total_amount=50, currency="XTR", telegram_charge_id="charge"
        )
    assert error_calls[0][0] == "charge"

    patch_success_pipeline(monkeypatch)
    monkeypatch.setattr(stars, "_payment_row", lambda _charge: {"side_effects_done_at_utc": "done"})
    duplicate = stars.record_successful_stars_payment(
        user_id=7, payload="p", total_amount=50, currency="XTR", telegram_charge_id="charge"
    )
    assert duplicate.duplicate is True

    patch_success_pipeline(monkeypatch, gift=True)
    monkeypatch.setattr(stars, "_payment_row", lambda _charge: {})
    gift_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(stars, "mark_gift_paid", lambda **kwargs: gift_calls.append(kwargs))
    done: list[str] = []
    events: list[Any] = []
    monkeypatch.setattr(stars, "_mark_payment_done", lambda charge: done.append(charge))
    monkeypatch.setattr(stars, "log_event", lambda *args: events.append(args))
    result = stars.record_successful_stars_payment(
        user_id=7, payload="p", total_amount=50, currency="XTR", telegram_charge_id="gift-charge"
    )
    assert result.gift_token == "gift_abc"
    assert gift_calls[0]["provider"] == "telegram_stars"
    assert done == ["gift-charge"]

    patch_success_pipeline(monkeypatch)
    monkeypatch.setattr(stars, "_payment_row", lambda _charge: {})
    monkeypatch.setattr(
        stars,
        "grant_tokens_for_payment",
        lambda **_kwargs: (True, SimpleNamespace(available_tokens=9), 1),
    )
    monkeypatch.setattr(
        stars,
        "grant_premium_entitlements_for_payment",
        lambda **_kwargs: SimpleNamespace(outbox_created=2, consultation_request_created=True),
    )
    result = stars.record_successful_stars_payment(
        user_id=7, payload="p", total_amount=50, currency="XTR", telegram_charge_id="personal"
    )
    assert result.wallet_balance == 9
    assert result.premium_outbox_created == 2
    assert result.consultation_request_created is True

    patch_success_pipeline(monkeypatch)
    monkeypatch.setattr(stars, "_payment_row", lambda _charge: {})
    monkeypatch.setattr(
        stars,
        "grant_tokens_for_payment",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("grant failed")),
    )
    error_calls.clear()
    monkeypatch.setattr(stars, "_mark_payment_error", lambda *args: error_calls.append(args))
    with pytest.raises(stars.StarsPaymentError, match="processing_failed"):
        stars.record_successful_stars_payment(
            user_id=7, payload="p", total_amount=50, currency="XTR", telegram_charge_id="broken"
        )
    assert error_calls[0][0] == "broken"
