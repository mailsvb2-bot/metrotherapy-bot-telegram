from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from services.payments import hooks


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


class Pre:
    def __init__(self, payload: str = "sub:1", currency: str = "RUB", amount: Any = 100) -> None:
        self.invoice_payload = payload
        self.currency = currency
        self.total_amount = amount
        self.from_user = SimpleNamespace(id=7)
        self.answers: list[dict[str, Any]] = []

    async def answer(self, **kwargs: Any) -> None:
        self.answers.append(kwargs)


class Message:
    def __init__(self, *, user_id: int | None = 1, username: str | None = "user", payment: Any = None, bot: Any = None) -> None:
        self.from_user = SimpleNamespace(id=user_id, username=username) if user_id is not None else None
        self.successful_payment = payment
        self.bot = bot
        self.answers: list[tuple[str, Any]] = []

    async def answer(self, text: str, reply_markup: Any = None) -> None:
        self.answers.append((text, reply_markup))


def test_payment_helper_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    message = Message(user_id=5, username="name")
    assert hooks._message_user_id(message) == 5
    assert hooks._message_username(message) == "name"
    assert hooks._message_user_id(Message(user_id=None)) is None
    assert hooks._message_username(Message(user_id=None)) is None

    values = hooks.payment_insert_values(
        user_id=1, telegram_charge_id="t", provider_charge_id="p", payload="sub:1",
        amount=10, currency="RUB", created_at="now", decision_id="d", correlation_id="c",
    )
    assert values == (1, "t", "p", "sub:1", 10, "RUB", "now", "d", "c")
    assert hooks._base_payment_payload(" sub:1|d=x|c=y ") == "sub:1"
    assert hooks._base_payment_payload("gift:x") == "gift:x"
    assert hooks._base_payment_payload(None) == ""

    assert hooks._row_value(None, "x", 0, "d") == "d"
    assert hooks._row_value({"x": 2}, "x", 0) == 2
    assert hooks._row_value((3,), "x", 0) == 3
    assert hooks._row_value((), "x", 2, "d") == "d"

    monkeypatch.setattr(hooks, "amount_minor_from_plan", lambda plan: int(plan["minor"]))
    assert hooks._expected_minor_amount_from_plan(None) == 0
    assert hooks._expected_minor_amount_from_plan({"is_active": False}) == 0
    assert hooks._expected_minor_amount_from_plan({"is_active": True, "minor": 123}) == 123

    def invalid(_plan: Any) -> int:
        raise hooks.PaymentAmountError("bad")

    monkeypatch.setattr(hooks, "amount_minor_from_plan", invalid)
    assert hooks._expected_minor_amount_from_plan({"is_active": True}) == 0


def test_gift_plan_lookup_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    class Conn:
        def __init__(self, row: Any) -> None:
            self.row = row

        def execute(self, *_args: Any) -> Any:
            return SimpleNamespace(fetchone=lambda: self.row)

    for row, expected in [
        (None, 0),
        ({"plan_id": 3, "paid": 1, "status": "paid"}, 0),
        ({"plan_id": 3, "paid": "bad", "status": "new"}, 0),
        ({"plan_id": "bad", "paid": 0, "status": "new"}, 0),
        ((4, 0, "new"), 4),
    ]:
        monkeypatch.setattr(hooks, "db", lambda row=row: DbContext(Conn(row)))
        assert hooks._gift_plan_id_by_code("code") == expected


def test_validate_pre_checkout_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks, "get_plan_by_id", lambda plan_id: {"is_active": True, "minor": plan_id * 100})
    monkeypatch.setattr(hooks, "amount_minor_from_plan", lambda plan: plan["minor"])
    monkeypatch.setattr(hooks, "_gift_plan_id_by_code", lambda code: 2 if code == "ok" else 0)

    assert "рублях" in hooks.validate_pre_checkout_invoice(payload="sub:1", currency="USD", total_amount=100)
    assert "некорректная" in hooks.validate_pre_checkout_invoice(payload="sub:1", currency="RUB", total_amount="bad")
    assert "больше нуля" in hooks.validate_pre_checkout_invoice(payload="sub:1", currency="RUB", total_amount=0)
    assert "подарочный код" in hooks.validate_pre_checkout_invoice(payload="gift:", currency="RUB", total_amount=100)
    assert "устарел" in hooks.validate_pre_checkout_invoice(payload="gift:no", currency="RUB", total_amount=100)
    assert "неизвестный" in hooks.validate_pre_checkout_invoice(payload="other", currency="RUB", total_amount=100)
    assert "тариф не найден" in hooks.validate_pre_checkout_invoice(payload="sub:bad", currency="RUB", total_amount=100)
    assert "Цена изменилась" in hooks.validate_pre_checkout_invoice(payload="sub:1", currency="RUB", total_amount=200)
    assert hooks.validate_pre_checkout_invoice(payload="sub:1|d=x", currency="rub", total_amount=100) is None
    assert hooks.validate_pre_checkout_invoice(payload="gift:ok", currency="RUB", total_amount=200) is None

    monkeypatch.setattr(hooks, "get_plan_by_id", lambda _plan_id: None)
    assert "недоступен" in hooks.validate_pre_checkout_invoice(payload="sub:1", currency="RUB", total_amount=100)


@pytest.mark.asyncio
async def test_pre_checkout_answers_and_failure_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    pre = Pre()

    async def direct(func: Any, **kwargs: Any) -> Any:
        return func(**kwargs)

    monkeypatch.setattr(hooks.asyncio, "to_thread", direct)
    monkeypatch.setattr(hooks, "validate_pre_checkout_invoice", lambda **_kwargs: None)
    await hooks.pre_checkout(pre)
    assert pre.answers[-1] == {"ok": True}

    monkeypatch.setattr(hooks, "validate_pre_checkout_invoice", lambda **_kwargs: "reject")
    await hooks.pre_checkout(pre)
    assert pre.answers[-1] == {"ok": False, "error_message": "reject"}

    for exc in (
        sqlite3.OperationalError("db"),
        ValueError("value"),
        RuntimeError("runtime"),
    ):
        def fail(**_kwargs: Any) -> None:
            raise exc

        monkeypatch.setattr(hooks, "validate_pre_checkout_invoice", fail)
        await hooks.pre_checkout(pre)
        assert pre.answers[-1]["ok"] is False
        assert "временно" in pre.answers[-1]["error_message"]

    class TelegramFailure(hooks.TelegramAPIError):
        pass

    class BrokenPre(Pre):
        async def answer(self, **kwargs: Any) -> None:
            raise asyncio.TimeoutError

    monkeypatch.setattr(hooks, "validate_pre_checkout_invoice", lambda **_kwargs: None)
    await hooks.pre_checkout(BrokenPre())
    await hooks._answer_pre_checkout_temporarily_unavailable(BrokenPre())


def test_paid_payload_parser() -> None:
    assert hooks._parse_paid_payload("sub:1") == ("sub:1", None, None)
    assert hooks._parse_paid_payload("sub:1|d=decision|c=correlation") == (
        "sub:1", "decision", "correlation"
    )
    assert hooks._parse_paid_payload("sub:1|d=|c=") == ("sub:1", None, None)


class QueryConn:
    def __init__(self, row: Any = None, *, error: BaseException | None = None) -> None:
        self.row = row
        self.error = error
        self.calls: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = ()) -> Any:
        self.calls.append((query, params))
        if self.error:
            raise self.error
        return SimpleNamespace(fetchone=lambda: self.row)


def test_followup_funnel_referral_and_ping_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 20, 12, 0)
    monkeypatch.setattr(hooks, "utc_now", lambda: now.replace(tzinfo=timezone.utc))
    jobs: list[tuple[Any, ...]] = []
    monkeypatch.setattr(hooks, "add_job", lambda *args: jobs.append(args))
    future = (now + timedelta(days=10)).isoformat()
    monkeypatch.setattr(hooks, "db", lambda: DbContext(QueryConn({"expires_at": future})))
    hooks._schedule_subscription_jobs(1)
    assert [job[1] for job in jobs] == ["sub_expiring_soon", "funnel2_expired_return_3d"]

    monkeypatch.setattr(hooks, "db", lambda: DbContext(QueryConn(None)))
    hooks._schedule_subscription_jobs(1)
    monkeypatch.setattr(hooks, "db", lambda: DbContext(QueryConn(error=sqlite3.OperationalError("db"))))
    hooks._schedule_subscription_jobs(1)

    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(hooks, "cancel_funnel", lambda uid: calls.append(("f1", uid)))
    import services.jobs

    monkeypatch.setattr(services.jobs, "cancel_funnel2", lambda uid: calls.append(("f2", uid)))
    monkeypatch.setattr(hooks, "clear_plan", lambda uid: calls.append(("clear", uid)))
    hooks._cancel_paid_user_funnels(5)
    assert calls == [("f1", 5), ("f2", 5), ("clear", 5)]

    monkeypatch.setattr(hooks, "cancel_jobs", lambda uid, prefix: calls.append((prefix, uid)))
    hooks._schedule_after_paid_setup_ping(5)
    assert any(call[0] == "after_paid_setup_ping" for call in calls)

    monkeypatch.setattr(hooks, "get_referrer", lambda _uid: None)
    assert hooks._apply_referral_bonus(1, None, {"days": 30}) is None
    monkeypatch.setattr(hooks, "get_referrer", lambda _uid: 9)
    monkeypatch.setattr(hooks, "reward_already_given", lambda _uid: False)
    monkeypatch.setattr(hooks, "can_reward_referrer", lambda _uid: True)
    grants: list[tuple[Any, ...]] = []
    monkeypatch.setattr(hooks, "grant", lambda *args: grants.append(args))
    monkeypatch.setattr(hooks, "mark_reward_given", lambda *args: grants.append(args))
    monkeypatch.setattr(
        hooks,
        "settings",
        SimpleNamespace(REF_BONUS_MONTH_DAYS=7, REF_BONUS_WEEK_DAYS=2),
    )
    result = hooks._apply_referral_bonus(1, "buyer", {"days": 30})
    assert result == {"referrer": 9, "bonus": 7, "buyer_tag": "@buyer", "period": "1 месяц"}
    result = hooks._apply_referral_bonus(2, None, {"days": 7})
    assert result == {"referrer": 9, "bonus": 2, "buyer_tag": "пользователь 2", "period": "1 неделю"}

    monkeypatch.setattr(hooks, "get_referrer", lambda _uid: (_ for _ in ()).throw(sqlite3.OperationalError("db")))
    assert hooks._apply_referral_bonus(1, None, {"days": 1}) is None


class PaymentConn:
    def __init__(self, *, duplicate: bool = False, gift: Any = None, fail: bool = False) -> None:
        self.duplicate = duplicate
        self.gift = gift
        self.fail = fail
        self.calls: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = ()) -> Any:
        self.calls.append((query, params))
        if self.fail and query == "BEGIN":
            raise sqlite3.OperationalError("transaction")
        if query.startswith("SELECT changes"):
            n = 0 if self.duplicate else 1
            return SimpleNamespace(fetchone=lambda: {"n": n})
        if query.startswith("SELECT days"):
            return SimpleNamespace(fetchone=lambda: self.gift)
        return SimpleNamespace(fetchone=lambda: None, rowcount=1)


def install_record_side_effects(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(hooks, "log_event", lambda *_args, **_kwargs: calls.append("event"))
    monkeypatch.setattr(hooks, "grant_tx", lambda *_args, **_kwargs: calls.append("grant_tx"))
    monkeypatch.setattr(hooks, "mark_gift_paid_tx", lambda *_args, **_kwargs: calls.append("gift_paid"))
    monkeypatch.setattr(hooks, "_schedule_subscription_jobs", lambda _uid: calls.append("schedule"))
    monkeypatch.setattr(hooks, "_cancel_paid_user_funnels", lambda _uid: calls.append("cancel"))
    monkeypatch.setattr(hooks, "_apply_referral_bonus", lambda *_args: {"referrer": 8})
    monkeypatch.setattr(hooks, "_schedule_after_paid_setup_ping", lambda _uid: calls.append("ping"))
    return calls


def test_record_payment_duplicate_gift_subscription_and_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_record_side_effects(monkeypatch)
    duplicate = PaymentConn(duplicate=True)
    monkeypatch.setattr(hooks, "db", lambda: DbContext(duplicate))
    result = hooks._record_successful_payment_sync(
        user_id=1, username=None, raw_payload="sub:1", total_amount=100,
        currency="RUB", charge_id="charge", provider_id="provider",
    )
    assert result["duplicate"] is True

    gift = PaymentConn(gift=(30, 77))
    monkeypatch.setattr(hooks, "db", lambda: DbContext(gift))
    result = hooks._record_successful_payment_sync(
        user_id=1, username=None, raw_payload="gift:CODE", total_amount=100,
        currency="RUB", charge_id="charge", provider_id="provider",
    )
    assert result["gift_code"] == "CODE"
    assert "gift_paid" in calls and "grant_tx" in calls

    tx = PaymentConn()
    paid_at = QueryConn()
    contexts = iter([tx, paid_at])
    monkeypatch.setattr(hooks, "db", lambda: DbContext(next(contexts)))
    monkeypatch.setattr(hooks, "get_plan_by_id", lambda plan_id: {"id": plan_id, "scope": "both", "days": 30})
    monkeypatch.setattr(hooks, "get_plan_id", lambda _uid: 2)
    result = hooks._record_successful_payment_sync(
        user_id=1, username="buyer", raw_payload="sub:bad|d=d|c=c", total_amount=100,
        currency="RUB", charge_id="", provider_id="provider",
    )
    assert result["plan"]["id"] == 2
    assert result["referral"] == {"referrer": 8}
    assert set(("schedule", "cancel", "ping")).issubset(calls)

    failing = PaymentConn(fail=True)
    monkeypatch.setattr(hooks, "db", lambda: DbContext(failing))
    with pytest.raises(sqlite3.OperationalError):
        hooks._record_successful_payment_sync(
            user_id=1, username=None, raw_payload="sub:1", total_amount=100,
            currency="RUB", charge_id="charge", provider_id="provider",
        )


@pytest.mark.asyncio
async def test_successful_payment_user_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks, "kb_after_paid", lambda: "after")
    no_payment = Message(payment=None)
    await hooks.successful_payment(no_payment)
    await hooks.successful_payment(Message(user_id=None, payment=SimpleNamespace()))

    async def direct(func: Any, **kwargs: Any) -> Any:
        return func(**kwargs)

    monkeypatch.setattr(hooks.asyncio, "to_thread", direct)
    results = iter([
        {"duplicate": True},
        {"duplicate": False, "gift_code": "GIFT"},
        {"duplicate": False, "gift_code": None, "plan": {"id": 1}, "referral": {"referrer": 9, "buyer_tag": "@b", "period": "1 месяц", "bonus": 7}},
        {"duplicate": False, "gift_code": None, "plan": None},
    ])
    monkeypatch.setattr(hooks, "_record_successful_payment_sync", lambda **_kwargs: next(results))
    delivered: list[str] = []
    monkeypatch.setattr(hooks, "deliver_gift_message", lambda message, code: delivered.append(code) or asyncio.sleep(0))

    payment = SimpleNamespace(
        invoice_payload="sub:1", telegram_payment_charge_id="t",
        provider_payment_charge_id="p", total_amount=100, currency="RUB",
    )
    await hooks.successful_payment(Message(payment=payment))
    gift_message = Message(payment=payment)
    await hooks.successful_payment(gift_message)
    assert delivered == ["GIFT"]

    class Bot:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail
            self.sent: list[Any] = []

        async def send_message(self, *args: Any) -> None:
            if self.fail:
                raise asyncio.TimeoutError
            self.sent.append(args)

    paid = Message(payment=payment, bot=Bot())
    await hooks.successful_payment(paid)
    assert paid.bot.sent and paid.answers[-1][1] == "after"

    generic = Message(payment=payment)
    await hooks.successful_payment(generic)
    assert generic.answers == [("✅ Оплата прошла.", "after")]
