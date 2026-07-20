from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.messenger import audio_delivery as delivery
from services.messenger.outbound import DeliveryPlan, SenderRegistry, UnsupportedMessengerDelivery


class Sender:
    def __init__(self, *, audio_exc: BaseException | None = None, text_exc: BaseException | None = None) -> None:
        self.audio_exc = audio_exc
        self.text_exc = text_exc
        self.audio_calls: list[tuple[Any, ...]] = []
        self.text_calls: list[tuple[Any, ...]] = []

    async def send_audio_file(self, external_user_id: str, file_path: Path, **kwargs: Any) -> str:
        self.audio_calls.append((external_user_id, file_path, kwargs))
        if self.audio_exc:
            raise self.audio_exc
        return "audio-ok"

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any) -> str:
        self.text_calls.append((external_user_id, text, kwargs))
        if self.text_exc:
            raise self.text_exc
        return "text-ok"


def item(anchor: int = 2, suffix: str = ".mp3") -> SimpleNamespace:
    return SimpleNamespace(anchor=anchor, title=f"Track {anchor}", path=Path(f"track-{anchor}{suffix}"))


def snapshot(**kwargs: Any) -> SimpleNamespace:
    values = {"pending_item": None, "last_anchor": None, "last_title": ""}
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_audio_delivery_text_and_keyboard_helpers() -> None:
    err = delivery._native_audio_failure_meta(ValueError("x" * 900))
    decoded = json.loads(err)
    assert decoded["error_type"] == "ValueError"
    assert len(decoded["error"]) == 700

    assert delivery._platform_name("vk") == "ВКонтакте"
    assert delivery._platform_name("max") == "MAX"
    assert delivery._platform_name("telegram") == "Telegram"
    assert delivery._platform_name("other") == "other"

    done = snapshot(last_anchor=5, last_title="Final")
    assert "ВКонтакте" in delivery._queue_finished_message("vk", done)
    assert "MAX" in delivery._queue_finished_message("max", done)
    assert "Последний подтверждённый" in delivery._queue_finished_message("telegram", done)
    assert "Последний подтверждённый" not in delivery._queue_finished_message("telegram", snapshot())

    keyboard = json.loads(delivery._vk_post_audio_keyboard_json())
    assert keyboard["buttons"][0][0]["action"]["label"] == "✅ Прослушал"
    assert "keyboard_json" in delivery.post_audio_control_kwargs("vk")
    assert delivery.post_audio_control_kwargs("max") == {}

    current = item()
    assert "Отправил файл" in delivery._pending_caption("vk", current)
    assert "Повторно отправил" in delivery._pending_caption("vk", current, replay=True)
    assert "отправлено прямо" in delivery.post_audio_controls_text("max", current)
    assert "Повторно отправил" in delivery.post_audio_controls_text("vk", current, replay=True)
    assert "безопасную ссылку" in delivery._vk_audio_access_link_text(current, "https://audio")
    assert "Повтор аудио" in delivery._vk_audio_access_link_text(current, "https://audio", replay=True)

    assert delivery._vk_file_is_native_audio(item(suffix=".OGG").path)
    assert not delivery._vk_file_is_native_audio(item(suffix=".mp3").path)


def test_replay_item_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    current = item(4)
    monkeypatch.setattr(delivery, "get_audio_item_by_anchor", lambda anchor: current if int(anchor) == 4 else None)

    assert delivery._replay_item_for_finished_queue("telegram", snapshot(last_anchor=4)) is None
    assert delivery._replay_item_for_finished_queue("vk", snapshot()) is None
    assert delivery._replay_item_for_finished_queue("vk", snapshot(last_anchor="bad")) is None
    assert delivery._replay_item_for_finished_queue("vk", snapshot(last_anchor=4)) is current

    assert delivery._explicit_replay_item(snapshot(), anchor=4) is current
    assert delivery._explicit_replay_item(snapshot(), anchor="bad") is None
    assert delivery._explicit_replay_item(snapshot(), anchor=99) is None
    pending = item(3)
    assert delivery._explicit_replay_item(snapshot(pending_item=pending), anchor=99) is pending
    assert delivery._explicit_replay_item(snapshot(last_anchor=4)) is current
    assert delivery._explicit_replay_item(snapshot(last_anchor="bad")) is None


@pytest.mark.asyncio
async def test_prepare_native_audio_path(monkeypatch: pytest.MonkeyPatch) -> None:
    max_item = item(1)
    monkeypatch.setattr(delivery, "ensure_max_opus_file", lambda path: Path("max.opus"))
    monkeypatch.setattr(delivery, "ensure_vk_opus_file", lambda path: Path("vk.opus"))

    assert await delivery._prepare_native_audio_path("max", max_item) == Path("max.opus")
    assert await delivery._prepare_native_audio_path("vk", item(2, ".ogg")) == Path("track-2.ogg")
    assert await delivery._prepare_native_audio_path("vk", item(3, ".mp3")) == Path("vk.opus")
    telegram_item = item(4)
    assert await delivery._prepare_native_audio_path("telegram", telegram_item) is telegram_item.path


@pytest.mark.asyncio
async def test_vk_audio_access_link_guards_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    current = item()
    sender = Sender()
    monkeypatch.setattr(delivery, "settings", SimpleNamespace(MESSENGER_PUBLIC_BASE_URL=""))
    with pytest.raises(UnsupportedMessengerDelivery, match="PUBLIC_BASE_URL"):
        await delivery.send_vk_audio_access_link(
            user_id=7, external_user_id="vk-7", sender=sender, item=current
        )

    monkeypatch.setattr(delivery, "settings", SimpleNamespace(MESSENGER_PUBLIC_BASE_URL="https://public"))
    monkeypatch.setattr(delivery, "issue_or_reuse_audio_access_token", lambda *_args, **_kwargs: "token")
    monkeypatch.setattr(delivery, "build_audio_access_url", lambda _token: "")
    with pytest.raises(UnsupportedMessengerDelivery, match="cannot be built"):
        await delivery.send_vk_audio_access_link(
            user_id=7, external_user_id="vk-7", sender=sender, item=current
        )

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(delivery, "build_audio_access_url", lambda _token: "https://public/audio/token")
    monkeypatch.setattr(delivery, "log_audio_timeline_event", lambda *_args, **kwargs: events.append(kwargs))
    result = await delivery.send_vk_audio_access_link(
        user_id=7, external_user_id="vk-7", sender=sender, item=current, replay=True
    )
    assert result.transport == "vk_audio_access_link_replay"
    assert sender.text_calls[-1][0] == "vk-7"
    assert "https://public/audio/token" in sender.text_calls[-1][1]
    assert events[-1]["event_type"] == "vk_audio_access_link_replayed"


@pytest.mark.asyncio
async def test_non_telegram_native_success_and_notice_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    current = item()
    sender = Sender(text_exc=RuntimeError("notice failed"))
    marks: list[Any] = []
    events: list[dict[str, Any]] = []

    async def prepared(_platform: str, _item: Any) -> Path:
        return Path("prepared.opus")

    monkeypatch.setattr(delivery, "_prepare_native_audio_path", prepared)
    monkeypatch.setattr(delivery, "mark_pending_audio_delivery", lambda *args, **kwargs: marks.append((args, kwargs)))
    monkeypatch.setattr(delivery, "log_audio_timeline_event", lambda *_args, **kwargs: events.append(kwargs))

    assert await delivery._send_non_telegram_native(
        user_id=7,
        platform="telegram",
        external_user_id="7",
        sender=sender,
        item=current,
        pending=None,
    ) is None

    result = await delivery._send_non_telegram_native(
        user_id=7,
        platform="max",
        external_user_id="max-7",
        sender=sender,
        item=current,
        pending=None,
    )
    assert result is not None
    assert result.transport == "max_native_audio_pending"
    assert marks
    assert any(event["event_type"] == "post_audio_notice_failed" for event in events)


@pytest.mark.asyncio
async def test_non_telegram_native_failure_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    current = item()

    async def prepared(_platform: str, _item: Any) -> Path:
        return Path("prepared.opus")

    monkeypatch.setattr(delivery, "_prepare_native_audio_path", prepared)
    monkeypatch.setattr(delivery, "log_audio_timeline_event", lambda *_args, **_kwargs: None)

    fallback_result = delivery.AudioDeliveryResult(7, "vk", current, "link", "fallback")

    async def fallback(**_kwargs: Any) -> delivery.AudioDeliveryResult:
        return fallback_result

    monkeypatch.setattr(delivery, "_send_vk_audio_access_link", fallback)
    vk_result = await delivery._send_non_telegram_native(
        user_id=7,
        platform="vk",
        external_user_id="vk-7",
        sender=Sender(audio_exc=RuntimeError("upload failed")),
        item=current,
        pending=None,
    )
    assert vk_result is fallback_result

    with pytest.raises(UnsupportedMessengerDelivery, match="именно аудио-вложение"):
        await delivery._send_non_telegram_native(
            user_id=7,
            platform="max",
            external_user_id="max-7",
            sender=Sender(audio_exc=RuntimeError("upload failed")),
            item=current,
            pending=None,
        )


def patch_plan_and_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    *,
    platform: str,
    external_user_id: str | None,
    progress: Any,
) -> None:
    monkeypatch.setattr(
        delivery,
        "build_delivery_plan",
        lambda *_args, **_kwargs: DeliveryPlan(platform=platform, external_user_id=external_user_id, user_id=7),
    )
    monkeypatch.setattr(delivery, "get_progress_snapshot", lambda _uid: progress)


@pytest.mark.asyncio
async def test_send_next_finished_and_access_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_plan_and_snapshot(
        monkeypatch, platform="vk", external_user_id="vk-7", progress=snapshot(last_anchor=3, last_title="Done")
    )
    monkeypatch.setattr(delivery, "get_next_audio_item", lambda _uid: None)
    monkeypatch.setattr(delivery, "_replay_item_for_finished_queue", lambda *_args: None)
    finished = await delivery.send_next_audio_to_user(7, senders=SenderRegistry())
    assert finished.item is None
    assert finished.transport == "none"
    assert "Все доступные аудио" in finished.message

    current = item()
    monkeypatch.setattr(delivery, "get_next_audio_item", lambda _uid: current)
    monkeypatch.setattr(
        delivery,
        "_reserve_new_delivery",
        lambda *_args: delivery.PracticeAccessDecision(False, "hard", "insufficient", message="pay first"),
    )
    denied = await delivery.send_next_audio_to_user(7, senders=SenderRegistry())
    assert denied.item is None
    assert denied.message == "pay first"


@pytest.mark.asyncio
async def test_send_next_telegram_success_warning_and_failure_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    current = item()
    patch_plan_and_snapshot(
        monkeypatch, platform="telegram", external_user_id="tg-7", progress=snapshot()
    )
    monkeypatch.setattr(delivery, "get_next_audio_item", lambda _uid: current)
    decision = delivery.PracticeAccessDecision(True, "soft", "reserved", reservation_id="r1", warning="warning")
    monkeypatch.setattr(delivery, "_reserve_new_delivery", lambda *_args: decision)
    finished: list[tuple[bool, Any]] = []
    monkeypatch.setattr(
        delivery,
        "_finish_delivery_access",
        lambda value, delivered: finished.append((delivered, value)),
    )
    marks: list[Any] = []
    monkeypatch.setattr(delivery, "mark_pending_audio_delivery", lambda *args, **kwargs: marks.append((args, kwargs)))
    monkeypatch.setattr(delivery, "log_audio_timeline_event", lambda *_args, **_kwargs: None)

    async def send_ok(*_args: Any, **_kwargs: Any) -> str:
        return "ok"

    monkeypatch.setattr(delivery, "_send_telegram_audio", send_ok)
    result = await delivery.send_next_audio_to_user(
        7, senders=SenderRegistry(), telegram_bot=SimpleNamespace()
    )
    assert result.transport == "telegram_audio_pending"
    assert result.message.startswith("warning")
    assert marks
    assert finished[-1] == (True, decision)

    with pytest.raises(UnsupportedMessengerDelivery, match="bot instance"):
        await delivery.send_next_audio_to_user(7, senders=SenderRegistry(), telegram_bot=None)
    assert finished[-1] == (False, decision)

    patch_plan_and_snapshot(monkeypatch, platform="telegram", external_user_id=None, progress=snapshot())
    with pytest.raises(UnsupportedMessengerDelivery, match="No Telegram external id"):
        await delivery.send_next_audio_to_user(
            7, senders=SenderRegistry(), telegram_bot=SimpleNamespace()
        )
    assert finished[-1] == (False, decision)


@pytest.mark.asyncio
async def test_send_next_nontelegram_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    current = item()
    patch_plan_and_snapshot(monkeypatch, platform="max", external_user_id="max-7", progress=snapshot(pending_item=current))
    monkeypatch.setattr(delivery, "get_next_audio_item", lambda _uid: None)

    with pytest.raises(UnsupportedMessengerDelivery, match="No sender registered"):
        await delivery.send_next_audio_to_user(7, senders=SenderRegistry())

    sender = Sender()
    patch_plan_and_snapshot(monkeypatch, platform="max", external_user_id=None, progress=snapshot(pending_item=current))
    with pytest.raises(UnsupportedMessengerDelivery, match="No external user id"):
        await delivery.send_next_audio_to_user(7, senders=SenderRegistry(max=sender))

    patch_plan_and_snapshot(monkeypatch, platform="max", external_user_id="max-7", progress=snapshot(pending_item=current))
    expected = delivery.AudioDeliveryResult(7, "max", current, "max_native_audio_pending", "ok")

    async def native(**_kwargs: Any) -> delivery.AudioDeliveryResult:
        return expected

    monkeypatch.setattr(delivery, "_send_non_telegram_native", native)
    result = await delivery.send_next_audio_to_user(7, senders=SenderRegistry(max=sender))
    assert result is expected

    async def unsupported(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(delivery, "_send_non_telegram_native", unsupported)
    with pytest.raises(UnsupportedMessengerDelivery, match="именно аудио-вложение"):
        await delivery.send_next_audio_to_user(7, senders=SenderRegistry(max=sender))


@pytest.mark.asyncio
async def test_send_replay_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_plan_and_snapshot(monkeypatch, platform="telegram", external_user_id="tg-7", progress=snapshot())
    monkeypatch.setattr(delivery, "_explicit_replay_item", lambda *_args, **_kwargs: None)
    none_result = await delivery.send_replay_audio_to_user(7, senders=SenderRegistry())
    assert none_result.transport == "none"
    assert "Пока нет аудио" in none_result.message

    current = item(9)
    monkeypatch.setattr(delivery, "_explicit_replay_item", lambda *_args, **_kwargs: current)
    with pytest.raises(UnsupportedMessengerDelivery, match="requires bot instance"):
        await delivery.send_replay_audio_to_user(7, senders=SenderRegistry())

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(delivery, "log_audio_timeline_event", lambda *_args, **kwargs: events.append(kwargs))

    async def send_ok(*_args: Any, **_kwargs: Any) -> str:
        return "ok"

    monkeypatch.setattr(delivery, "_send_telegram_audio", send_ok)
    tg = await delivery.send_replay_audio_to_user(
        7, senders=SenderRegistry(), telegram_bot=SimpleNamespace()
    )
    assert tg.transport == "telegram_audio_replay"
    assert events[-1]["event_type"] == "telegram_audio_replayed"

    patch_plan_and_snapshot(monkeypatch, platform="vk", external_user_id="vk-7", progress=snapshot(pending_item=current))
    with pytest.raises(UnsupportedMessengerDelivery, match="No sender registered"):
        await delivery.send_replay_audio_to_user(7, senders=SenderRegistry())

    patch_plan_and_snapshot(monkeypatch, platform="vk", external_user_id=None, progress=snapshot(pending_item=current))
    with pytest.raises(UnsupportedMessengerDelivery, match="No external user id"):
        await delivery.send_replay_audio_to_user(7, senders=SenderRegistry(vk=Sender()))

    patch_plan_and_snapshot(monkeypatch, platform="vk", external_user_id="vk-7", progress=snapshot(pending_item=current))
    expected = delivery.AudioDeliveryResult(7, "vk", current, "vk_native_audio_replay", "ok")

    async def native(**_kwargs: Any) -> delivery.AudioDeliveryResult:
        return expected

    monkeypatch.setattr(delivery, "_send_non_telegram_native", native)
    assert await delivery.send_replay_audio_to_user(7, senders=SenderRegistry(vk=Sender())) is expected

    async def none_native(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(delivery, "_send_non_telegram_native", none_native)
    with pytest.raises(UnsupportedMessengerDelivery, match="именно аудио-вложение"):
        await delivery.send_replay_audio_to_user(7, senders=SenderRegistry(vk=Sender()))
