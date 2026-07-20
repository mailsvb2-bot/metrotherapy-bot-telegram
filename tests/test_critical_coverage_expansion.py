from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime import messenger_telegram_sender, messenger_transport_errors
from services import audio_dispatcher, auto_audio_entitlement, premium_delivery
from services.messenger import delivery_health
from services.payments import common as payment_common
from services.payments import gift, subscription
from services.validators import privacy as privacy_validator


class FakeMessage:
    def __init__(
        self,
        *,
        user_id: int | None = 1,
        full_name: str = "Иван",
        bot: Any = None,
        user_shared: Any = None,
        users_shared: Any = None,
    ) -> None:
        self.from_user = (
            SimpleNamespace(id=user_id, full_name=full_name) if user_id is not None else None
        )
        self.bot = bot
        self.user_shared = user_shared
        self.users_shared = users_shared
        self.answers: list[tuple[str, Any]] = []
        self.edits: list[tuple[str, Any]] = []

    async def answer(self, text: str, reply_markup: Any = None, **kwargs: Any) -> Any:
        self.answers.append((text, reply_markup))
        return SimpleNamespace(text=text, reply_markup=reply_markup, kwargs=kwargs)

    async def edit_text(self, text: str, reply_markup: Any = None, **kwargs: Any) -> Any:
        self.edits.append((text, reply_markup))
        return SimpleNamespace(text=text, reply_markup=reply_markup, kwargs=kwargs)


class FakeCallback:
    def __init__(self, message: Any, *, user_id: int = 1, full_name: str = "Иван") -> None:
        self.message = message
        self.from_user = SimpleNamespace(id=user_id, full_name=full_name)


class FakeDb:
    def __init__(self, row_or_rows: Any = None, *, error: BaseException | None = None) -> None:
        self.row_or_rows = row_or_rows
        self.error = error
        self.queries: list[str] = []

    def __enter__(self) -> "FakeDb":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, query: str, *_args: Any, **_kwargs: Any) -> "FakeDb":
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self

    def fetchall(self) -> Any:
        return self.row_or_rows

    def fetchone(self) -> Any:
        return self.row_or_rows


@pytest.mark.asyncio
async def test_premium_delivery_flush_covers_success_skip_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RuntimeFailSender:
        async def send_text(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("runtime")

    class OsFailSender:
        async def send_text(self, *_args: Any, **_kwargs: Any) -> None:
            raise OSError("os")

    ok = premium_delivery.MemorySender()
    failed_marks: list[tuple[int, str]] = []
    sent_marks: list[int] = []
    monkeypatch.setattr(
        premium_delivery,
        "pending_delivery",
        lambda *, limit: [
            {"id": 1, "platform": "max", "external_user_id": "10", "body": "ok"},
            {"id": 2, "platform": "missing", "external_user_id": "20", "body": "skip"},
            {"id": 3, "platform": "runtime", "external_user_id": "30", "body": "fail"},
            {"id": 4, "platform": "os", "external_user_id": "40", "body": "fail"},
            {"id": 5, "platform": "max", "external_user_id": " ", "body": "skip"},
        ],
    )
    monkeypatch.setattr(
        premium_delivery,
        "mark_delivery_failed",
        lambda delivery_id, reason: failed_marks.append((delivery_id, reason)),
    )
    monkeypatch.setattr(
        premium_delivery,
        "mark_delivery_sent",
        lambda delivery_id: sent_marks.append(delivery_id),
    )

    result = await premium_delivery.flush_premium_delivery_outbox(
        senders={"max": ok, "runtime": RuntimeFailSender(), "os": OsFailSender()},
        limit=5,
    )

    assert result == premium_delivery.PremiumDeliveryRunResult(sent=1, failed=2, skipped=2)
    assert ok.messages == [("10", "ok", {"disable_link_preview": True})]
    assert sent_marks == [1]
    assert [item[0] for item in failed_marks] == [2, 3, 4, 5]
    assert premium_delivery.PremiumDeliveryRunResult() == premium_delivery.PremiumDeliveryRunResult(
        sent=0,
        failed=0,
        skipped=0,
    )


@pytest.mark.asyncio
async def test_audio_dispatcher_validates_path_and_sends(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Bot:
        def __init__(self) -> None:
            self.calls: list[tuple[int, Any]] = []

        async def send_audio(self, chat_id: int, *, audio: Any) -> None:
            self.calls.append((chat_id, audio))

    missing = tmp_path / "missing.mp3"
    with pytest.raises(FileNotFoundError, match="missing.mp3"):
        await audio_dispatcher.send_audio_fast(Bot(), 1, missing)

    existing = tmp_path / "audio.mp3"
    existing.write_bytes(b"audio")
    monkeypatch.setattr(audio_dispatcher, "FSInputFile", lambda path: ("file", Path(path)))
    bot = Bot()
    await audio_dispatcher.send_audio_fast(bot, 7, existing)
    assert bot.calls == [(7, ("file", existing))]


def test_auto_audio_entitlement_bulk_sources_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert auto_audio_entitlement.subscription_user_ids("night") == []

    morning_db = FakeDb([(3,), (1,), (3,)])
    monkeypatch.setattr(auto_audio_entitlement, "db", lambda: morning_db)
    assert auto_audio_entitlement.subscription_user_ids("morning") == [3, 1, 3]
    assert "total_morning" in morning_db.queries[0]

    evening_db = FakeDb([(4,), (2,)])
    monkeypatch.setattr(auto_audio_entitlement, "db", lambda: evening_db)
    assert auto_audio_entitlement.subscription_user_ids("evening") == [4, 2]
    assert "total_evening" in evening_db.queries[0]

    monkeypatch.setattr(
        auto_audio_entitlement,
        "db",
        lambda: FakeDb(error=sqlite3.OperationalError("db")),
    )
    assert auto_audio_entitlement.subscription_user_ids("morning") == []

    monkeypatch.setattr(auto_audio_entitlement, "token_economy_enabled", lambda: False)
    monkeypatch.setattr(auto_audio_entitlement, "enforcement_mode", lambda: "hard")
    assert auto_audio_entitlement.practice_wallet_user_ids() == []

    monkeypatch.setattr(auto_audio_entitlement, "token_economy_enabled", lambda: True)
    monkeypatch.setattr(auto_audio_entitlement, "enforcement_mode", lambda: "off")
    assert auto_audio_entitlement.practice_wallet_user_ids() == []

    wallet_db = FakeDb([(9,), (7,), (9,)])
    monkeypatch.setattr(auto_audio_entitlement, "enforcement_mode", lambda: "hard")
    monkeypatch.setattr(auto_audio_entitlement, "db", lambda: wallet_db)
    assert auto_audio_entitlement.practice_wallet_user_ids() == [9, 7, 9]

    monkeypatch.setattr(
        auto_audio_entitlement,
        "db",
        lambda: FakeDb(error=sqlite3.DatabaseError("wallet")),
    )
    assert auto_audio_entitlement.practice_wallet_user_ids() == []

    monkeypatch.setattr(auto_audio_entitlement, "practice_wallet_user_ids", lambda: [9, 7, 9])
    assert auto_audio_entitlement.eligible_user_ids("morning") == [7, 9]

    monkeypatch.setattr(auto_audio_entitlement, "token_economy_enabled", lambda: False)
    monkeypatch.setattr(auto_audio_entitlement, "subscription_user_ids", lambda _slot: [4, 2, 4])
    assert auto_audio_entitlement.eligible_user_ids("evening") == [2, 4]


@pytest.mark.parametrize(
    ("error", "mode", "expected"),
    [
        (sqlite3.OperationalError("db"), "soft", True),
        (sqlite3.OperationalError("db"), "hard", False),
        (TypeError("type"), "soft", True),
        (ValueError("value"), "hard", False),
    ],
)
def test_auto_audio_entitlement_point_checks_fail_closed_or_soft(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    mode: str,
    expected: bool,
) -> None:
    monkeypatch.setattr(auto_audio_entitlement, "enforcement_mode", lambda: mode)

    def fail(*_args: Any) -> bool:
        raise error

    monkeypatch.setattr(auto_audio_entitlement, "has_access", fail)
    assert auto_audio_entitlement.has_entitlement(10, "morning") is expected


def test_auto_audio_entitlement_normalizes_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        auto_audio_entitlement,
        "has_access",
        lambda user_id, slot: calls.append((user_id, slot)) or 1,
    )
    assert auto_audio_entitlement.has_entitlement("12", "morning") is True
    assert auto_audio_entitlement.has_entitlement(13, "anything") is True
    assert calls == [(12, "morning"), (13, "evening")]


def test_delivery_health_age_and_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(delivery_health, "utc_now", lambda: fixed)

    assert delivery_health._row_value({"a": 3}, "a", 0) == 3
    assert delivery_health._row_value((4,), "a", 0) == 4
    assert delivery_health._age_sec(None) == 0
    assert delivery_health._age_sec("") == 0
    assert delivery_health._age_sec("bad-date") == 0
    assert delivery_health._age_sec(fixed - timedelta(seconds=15)) == 15
    assert delivery_health._age_sec(datetime(2026, 7, 20, 11, 59, 30)) == 30
    assert delivery_health._age_sec(fixed + timedelta(seconds=5)) == 0

    monkeypatch.setattr(delivery_health, "db", lambda: FakeDb(None))
    assert delivery_health._queue_age_snapshot() == {
        "oldest_pending_age_sec": 0,
        "oldest_retry_age_sec": 0,
        "oldest_sending_age_sec": 0,
    }

    row = {
        "oldest_pending": (fixed - timedelta(seconds=10)).isoformat(),
        "oldest_retry": fixed - timedelta(seconds=20),
        "oldest_sending": "invalid",
    }
    monkeypatch.setattr(delivery_health, "db", lambda: FakeDb(row))
    assert delivery_health._queue_age_snapshot() == {
        "oldest_pending_age_sec": 10,
        "oldest_retry_age_sec": 20,
        "oldest_sending_age_sec": 0,
    }

    monkeypatch.setattr(
        delivery_health.delivery_outbox,
        "outbox_snapshot",
        lambda: {"pending": "1", "retry": 2, "sending": 3, "sent": 4, "dead": 5},
    )
    monkeypatch.setattr(
        delivery_health.delivery_pool,
        "worker_snapshot",
        lambda: {"running": True, "workers": 2},
    )
    monkeypatch.setattr(
        delivery_health,
        "_queue_age_snapshot",
        lambda: {"oldest_pending_age_sec": 7},
    )
    assert delivery_health.delivery_health_snapshot() == {
        "running": True,
        "workers": 2,
        "pending": 1,
        "retry": 2,
        "sending": 3,
        "sent": 4,
        "dead": 5,
        "oldest_pending_age_sec": 7,
    }


@pytest.mark.asyncio
async def test_subscription_surfaces_and_legacy_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subscription, "Message", FakeMessage)
    monkeypatch.setattr(subscription, "kb_tariffs", lambda user_id: ("tariffs", user_id))
    monkeypatch.setattr(subscription, "kb_back", lambda callback: ("back", callback))
    events: list[tuple[int, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        subscription,
        "log_event",
        lambda uid, name, meta: events.append((uid, name, dict(meta))),
    )

    message = FakeMessage(user_id=11)
    assert subscription._message_user_id(FakeMessage(user_id=None)) is None
    assert subscription._message_user_id(message) == 11
    assert subscription._callback_message(FakeCallback(object())) is None
    assert subscription._callback_user_id(FakeCallback(message, user_id=11)) == 11

    await subscription.cmd_subscribe(message)
    assert message.answers[-1] == ("💳 Тарифы:", ("tariffs", 11))

    async def fake_to_thread(func: Any, user_id: int, surface: str) -> str:
        assert func is subscription.get_preface
        assert (user_id, surface) == (11, "sub")
        return "Привет. "

    monkeypatch.setattr(subscription.asyncio, "to_thread", fake_to_thread)
    callback = FakeCallback(message, user_id=11)
    await subscription.sub_menu(callback)
    assert message.edits[-1] == ("Привет. 💳 Выберите пакет практик:", ("tariffs", 11))
    assert [event[1] for event in events[-2:]] == ["view_tariffs", "sub_menu_open"]

    await subscription.sub_pick(callback)
    await subscription.pay_selected(callback)
    assert "старый способ оплаты отключён" in message.answers[-2][0]
    assert "старый способ оплаты отключён" in message.answers[-1][0]
    assert [event[2]["stage"] for event in events[-2:]] == ["sub_pick", "pay_selected"]

    invalid = FakeCallback(object(), user_id=11)
    before = (len(message.answers), len(message.edits), len(events))
    await subscription.sub_menu(invalid)
    await subscription.sub_pick(invalid)
    await subscription.pay_selected(invalid)
    assert before == (len(message.answers), len(message.edits), len(events))


@pytest.mark.asyncio
async def test_payment_common_contract_and_safe_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert payment_common.money_str_rub(10) == "10.00"
    assert payment_common.money_str_rub(-2) == "-2.00"

    monkeypatch.setattr(
        payment_common,
        "settings",
        SimpleNamespace(
            YOOKASSA_TAX_SYSTEM_CODE=2,
            YOOKASSA_VAT_CODE=1,
            YOOKASSA_PAYMENT_MODE="full_payment",
            YOOKASSA_PAYMENT_SUBJECT="service",
        ),
    )
    monkeypatch.setattr(
        payment_common,
        "validate_receipt_contract",
        lambda **kwargs: (
            kwargs["tax_system_code"],
            kwargs["vat_code"],
            kwargs["payment_mode"],
            kwargs["payment_subject"],
        ),
    )
    payload = json.loads(payment_common.yookassa_provider_data_receipt(" " * 3, 15))
    item = payload["receipt"]["items"][0]
    assert payload["receipt"]["tax_system_code"] == 2
    assert item["description"] == ""
    assert item["amount"] == {"value": "15.00", "currency": "RUB"}
    assert item["quantity"] == "1.00"

    assert payment_common.is_user_share_message(SimpleNamespace(user_shared=object())) is True
    assert payment_common.is_user_share_message(
        SimpleNamespace(user_shared=None, users_shared=object())
    ) is True
    assert payment_common.is_user_share_message(
        SimpleNamespace(user_shared=None, users_shared=None)
    ) is False

    keyboard = payment_common.invoice_link_kb("https://pay.example", back_cb="back")
    assert keyboard.inline_keyboard[0][0].url == "https://pay.example"
    assert keyboard.inline_keyboard[1][0].callback_data == "back"

    class Callback:
        async def answer(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    assert await payment_common.safe_answer_callback(Callback(), "ok") is True

    class FakeTelegramError(Exception):
        pass

    monkeypatch.setattr(payment_common, "TelegramBadRequest", FakeTelegramError)
    monkeypatch.setattr(payment_common, "TelegramNetworkError", FakeTelegramError)
    monkeypatch.setattr(payment_common, "TelegramAPIError", FakeTelegramError)

    class FailingCallback:
        async def answer(self, *_args: Any, **_kwargs: Any) -> None:
            raise FakeTelegramError("expired")

    assert await payment_common.safe_answer_callback(FailingCallback()) is False


def test_messenger_transport_errors_are_bounded_and_sanitized() -> None:
    exc = messenger_transport_errors.MessengerTransportError(
        "provider payload token=secret",
        code=" HTTP 500 / token=SECRET ",
    )
    assert exc.safe_code == "http_500_token_secret"
    assert "provider payload" not in messenger_transport_errors.safe_transport_error_text(exc)
    assert messenger_transport_errors.safe_transport_error_text(exc) == (
        "MessengerTransportError:http_500_token_secret"
    )

    huge = messenger_transport_errors.MessengerTransportError(code="x" * 500)
    assert len(huge.safe_code) == 120
    plain = ValueError("secret payload")
    assert messenger_transport_errors.safe_transport_error_text(plain) == "ValueError"
    assert isinstance(
        messenger_transport_errors.MessengerMediaNotReadyError(),
        messenger_transport_errors.MessengerTransportError,
    )
    assert isinstance(
        messenger_transport_errors.MessengerMediaTokenRejectedError(),
        messenger_transport_errors.MessengerTransportError,
    )


@pytest.mark.asyncio
async def test_telegram_sender_text_and_audio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Bot:
        def __init__(self) -> None:
            self.messages: list[tuple[int, str, dict[str, Any]]] = []

        async def send_message(self, user_id: int, text: str, **kwargs: Any) -> str:
            self.messages.append((user_id, text, kwargs))
            return "sent"

    bot = Bot()
    sender = messenger_telegram_sender.TelegramBotSender(bot)
    assert await sender.send_text("15", "hello", disable_notification=True) == "sent"
    assert bot.messages == [(15, "hello", {"disable_notification": True})]

    audio = tmp_path / "sample.mp3"
    audio.write_bytes(b"x")
    calls: list[tuple[Any, int, str, Path, str]] = []

    async def fake_send_audio_cached(
        passed_bot: Any,
        user_id: int,
        *,
        key: str,
        file_path: Path,
        caption: str,
    ) -> str:
        calls.append((passed_bot, user_id, key, file_path, caption))
        return "audio"

    import services.fast_send_audio

    monkeypatch.setattr(services.fast_send_audio, "send_audio_cached", fake_send_audio_cached)
    assert await sender.send_audio_file("16", audio, caption=None, ignored=True) == "audio"
    assert calls == [(bot, 16, "cross_audio:sample.mp3", audio, "")]


def test_privacy_validator_success_warning_and_strict_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(privacy_validator, "get_connection", lambda: FakeDb())
    monkeypatch.setattr(
        privacy_validator,
        "validate_privacy_manifest",
        lambda _conn, strict: SimpleNamespace(discovered_user_tables=["users", "events"]),
    )
    privacy_validator.validate_privacy_schema(strict=True)

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("manifest")

    monkeypatch.setattr(privacy_validator, "validate_privacy_manifest", fail)
    privacy_validator.validate_privacy_schema(strict=False)
    with pytest.raises(privacy_validator.ValidationError, match="manifest"):
        privacy_validator.validate_privacy_schema(strict=True)


@pytest.mark.asyncio
async def test_gift_menu_target_selection_and_legacy_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gift, "Message", FakeMessage)
    monkeypatch.setattr(gift, "kb_gift_tariffs", lambda **kwargs: ("tariffs", kwargs))
    monkeypatch.setattr(gift, "kb_back", lambda callback: ("back", callback))
    pending: list[tuple[int, str, dict[str, Any]]] = []
    events: list[tuple[int, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        gift,
        "set_pending",
        lambda uid, kind, meta: pending.append((uid, kind, dict(meta))),
    )
    monkeypatch.setattr(
        gift,
        "log_event",
        lambda uid, name, meta: events.append((uid, name, dict(meta))),
    )

    message = FakeMessage(user_id=21, full_name=" Иван ")
    callback = FakeCallback(message, user_id=21, full_name=" Иван ")
    await gift.gift_menu(callback)
    assert pending[-1] == (21, "gift_universal", {"from_name": "Иван"})
    assert events[-1][1] == "gift_menu"
    assert "Подарить подписку" in message.edits[-1][0]

    await gift.gift_menu(FakeCallback(object(), user_id=21))
    assert len(message.edits) == 1

    monkeypatch.setattr(gift, "pick_user_keyboard", lambda: None)
    await gift.gift_pick_target(callback)
    assert "не поддерживает выбор пользователя" in message.answers[-1][0]

    monkeypatch.setattr(gift, "pick_user_keyboard", lambda: "picker")
    await gift.gift_pick_target(callback)
    assert pending[-1] == (21, "gift_target", {"from_name": "Иван"})
    assert message.answers[-1][1] == "picker"

    await gift.gift_buy(callback)
    assert "старый способ оплаты подарка отключён" in message.answers[-1][0]
    assert events[-1][2]["stage"] == "gift_buy"
    await gift.gift_buy(FakeCallback(object(), user_id=21))


@pytest.mark.asyncio
async def test_gift_cancel_and_shared_user_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gift, "Message", FakeMessage)
    monkeypatch.setattr(gift, "ReplyKeyboardRemove", lambda: "remove")
    monkeypatch.setattr(gift, "kb_main", lambda **kwargs: ("main", kwargs))
    monkeypatch.setattr(gift, "kb_gift_tariffs", lambda **kwargs: ("tariffs", kwargs))
    monkeypatch.setattr(gift, "kb", lambda rows: rows)

    cleared: list[int] = []
    popped: list[int] = []
    targets: list[tuple[int, int]] = []
    events: list[tuple[int, str, dict[str, Any]]] = []
    monkeypatch.setattr(gift, "clear_target", lambda uid: cleared.append(uid))
    monkeypatch.setattr(gift, "pop_pending", lambda uid: popped.append(uid) or SimpleNamespace())
    monkeypatch.setattr(gift, "set_target", lambda uid, to_id: targets.append((uid, to_id)))
    monkeypatch.setattr(
        gift,
        "log_event",
        lambda uid, name, meta: events.append((uid, name, dict(meta))),
    )

    no_user = FakeMessage(user_id=None)
    await gift.gift_pick_cancel(no_user)
    assert no_user.answers == []

    message = FakeMessage(user_id=31)
    monkeypatch.setattr(gift, "peek_pending", lambda _uid: None)
    await gift.gift_pick_cancel(message)
    assert message.answers == []

    monkeypatch.setattr(gift, "peek_pending", lambda _uid: SimpleNamespace(kind="other"))
    await gift.gift_pick_cancel(message)
    assert message.answers == []

    monkeypatch.setattr(gift, "peek_pending", lambda _uid: SimpleNamespace(kind="gift_target"))
    await gift.gift_pick_cancel(message)
    assert popped[-1] == 31
    assert cleared[-1] == 31
    assert message.answers[-2][1] == "remove"
    assert message.answers[-1][1][0] == "main"

    class State:
        def __init__(self, error: BaseException | None = None) -> None:
            self.error = error
            self.calls = 0

        async def clear(self) -> None:
            self.calls += 1
            if self.error is not None:
                raise self.error

    await gift.gift_users_shared(no_user, State())
    monkeypatch.setattr(gift, "peek_pending", lambda _uid: None)
    await gift.gift_users_shared(message, State())
    assert targets == []

    monkeypatch.setattr(gift, "peek_pending", lambda _uid: SimpleNamespace(kind="gift_target"))
    monkeypatch.setattr(gift, "pop_pending", lambda _uid: None)
    await gift.gift_users_shared(message, State())
    assert targets == []

    monkeypatch.setattr(gift, "pop_pending", lambda uid: popped.append(uid) or SimpleNamespace())
    shared = FakeMessage(user_id=31, user_shared=SimpleNamespace(user_id=99))
    await gift.gift_users_shared(shared, State(sqlite3.OperationalError("state")))
    assert targets[-1] == (31, 99)
    assert events[-1] == (31, "gift_target_picked", {"to_id": 99})
    assert shared.answers[-1][1][0] == "tariffs"

    multi = FakeMessage(
        user_id=31,
        users_shared=SimpleNamespace(users=[SimpleNamespace(user_id=100)]),
    )
    await gift.gift_users_shared(multi, State(RuntimeError("state")))
    assert targets[-1] == (31, 100)

    invalid = FakeMessage(user_id=31)
    await gift.gift_users_shared(invalid, State())
    assert "Не удалось получить пользователя" in invalid.answers[-1][0]

    class BrokenId:
        @property
        def user_id(self) -> int:
            raise ValueError("bad")

    broken = FakeMessage(user_id=31, user_shared=BrokenId())
    await gift.gift_users_shared(broken, State())
    assert "Не удалось получить пользователя" in broken.answers[-1][0]


@pytest.mark.asyncio
async def test_gift_delivery_success_and_platform_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gift, "Message", FakeMessage)
    monkeypatch.setattr(gift, "get_gift_template", lambda: "От {from_name}: {link}")
    monkeypatch.setattr(gift, "_gift_share_keyboard", lambda code, text: ("share", code, text))
    monkeypatch.setattr(gift, "kb_main", lambda **kwargs: ("main", kwargs))
    events: list[tuple[int, str, dict[str, Any]]] = []
    cleared: list[int] = []
    monkeypatch.setattr(
        gift,
        "log_event",
        lambda uid, name, meta: events.append((uid, name, dict(meta))),
    )
    monkeypatch.setattr(gift, "clear_target", lambda uid: cleared.append(uid))

    no_user = FakeMessage(user_id=None)
    await gift.deliver_gift_message(no_user, "NONE")
    assert no_user.answers == []

    monkeypatch.setattr(gift, "get_target", lambda _uid: None)
    fallback = FakeMessage(user_id=41, full_name="")
    await gift.deliver_gift_message(fallback, "CODE")
    assert events[-1][1] == "gift_delivery_platform_choice"
    assert len(fallback.answers) == 2
    assert fallback.answers[0][1][:2] == ("share", "CODE")
    assert fallback.answers[1][1][0] == "main"
    assert cleared[-1] == 41

    class Bot:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.sent: list[tuple[int, str]] = []

        async def get_me(self) -> Any:
            return SimpleNamespace(username="metro")

        async def send_message(self, user_id: int, text: str) -> None:
            if self.fail:
                raise FakeTelegramError("provider")
            self.sent.append((user_id, text))

    class FakeTelegramError(Exception):
        pass

    monkeypatch.setattr(gift, "TelegramAPIError", FakeTelegramError)
    monkeypatch.setattr(gift, "get_target", lambda _uid: SimpleNamespace(to_id=77))
    success_bot = Bot()
    success = FakeMessage(user_id=42, full_name="Анна", bot=success_bot)
    await gift.deliver_gift_message(success, "OK")
    assert success_bot.sent == [(77, "От Анна: https://t.me/metro?start=gift_OK")]
    assert events[-1][1] == "gift_delivered_ok"
    assert len(success.answers) == 1

    failed = FakeMessage(user_id=43, full_name="Анна", bot=Bot(fail=True))
    await gift.deliver_gift_message(failed, "FAIL")
    assert events[-1][1] == "gift_delivery_platform_choice"
    assert len(failed.answers) == 2


def test_gift_helpers_and_share_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gift, "Message", FakeMessage)
    assert gift._callback_message(FakeCallback(object())) is None
    msg = FakeMessage(user_id=50, full_name=" Имя ")
    assert gift._callback_message(FakeCallback(msg)) is msg
    assert gift._message_user_id(msg) == 50
    assert gift._message_user_id(FakeMessage(user_id=None)) is None
    assert gift._message_user_full_name(msg) == "Имя"
    assert gift._message_user_full_name(FakeMessage(user_id=None)) == ""

    monkeypatch.setattr(
        gift,
        "build_gift_share_targets",
        lambda code, *, text: [
            {"title": "Telegram", "url": f"https://t.me/share?{code}:{text}"},
            {"title": "MAX", "url": f"https://max.ru/share?{code}:{text}"},
        ],
    )
    monkeypatch.setattr(gift, "InlineKeyboardButton", lambda **kwargs: kwargs)
    monkeypatch.setattr(gift, "kb", lambda rows: rows)
    keyboard = gift._gift_share_keyboard("ABC", "hello")
    assert keyboard[0][0]["text"] == "🎁 Отправить в Telegram"
    assert keyboard[1][0]["text"] == "🎁 Отправить в MAX"
    assert keyboard[-1][0]["callback_data"] == "menu:main"
