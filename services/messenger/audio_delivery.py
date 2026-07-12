from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from config.settings import settings
from runtime.messenger_transport_errors import MessengerTransportError
from services.messenger.audio_access import issue_or_reuse_audio_access_token
from services.messenger.audio_links import build_audio_access_url
from services.messenger.audio_progress import (
    AudioProgressItem,
    get_audio_item_by_anchor,
    get_next_audio_item,
    get_progress_snapshot,
    mark_pending_audio_delivery,
)
from services.messenger.max_audio import ensure_max_opus_file, ensure_vk_opus_file
from services.messenger.outbound import (
    SenderRegistry,
    UnsupportedMessengerDelivery,
    build_delivery_plan,
)
from services.messenger.platforms import MessengerPlatform
from services.messenger.timeline import log_audio_timeline_event
from services.practice_tokens import (
    PracticeAccessDecision,
    check_and_reserve_for_audio,
    finalize_audio_access,
)

NATIVE_AUDIO_REQUIRED_MESSAGE = (
    "⚠️ Не удалось отправить аудио прямо в этот мессенджер. "
    "Ссылку на аудио я не отправляю: по эталону пользовательского сценария здесь должно быть именно аудио-вложение. "
    "Попробуйте ещё раз позже или сообщите администратору."
)


def _native_audio_failure_meta(exc: BaseException) -> str:
    return json.dumps(
        {"error_type": type(exc).__name__, "error": str(exc)[:700]},
        ensure_ascii=False,
    )


async def _send_telegram_audio(bot: Any, external_user_id: str, item: AudioProgressItem) -> Any:
    from services.fast_send_audio import send_audio_cached

    return await send_audio_cached(
        bot,
        int(external_user_id),
        key=f"cross_audio:{item.path.name}",
        file_path=item.path,
        caption=f"🎧 Аудио №{item.anchor}: {item.title}",
    )


@dataclass(frozen=True)
class AudioDeliveryResult:
    user_id: int
    platform: str
    item: AudioProgressItem | None
    transport: str
    message: str


def _platform_name(platform: str) -> str:
    if platform == MessengerPlatform.VK.value:
        return "ВКонтакте"
    if platform == MessengerPlatform.MAX.value:
        return "MAX"
    if platform == MessengerPlatform.TELEGRAM.value:
        return "Telegram"
    return platform


def _queue_finished_message(platform: str, snapshot: Any) -> str:
    last = ""
    if getattr(snapshot, "last_anchor", None):
        last_title = getattr(snapshot, "last_title", "") or "последнее аудио"
        last = f"\n\nПоследний подтверждённый трек: №{snapshot.last_anchor} — {last_title}."

    if platform == MessengerPlatform.VK.value:
        return (
            "✅ Все доступные аудио в общей очереди уже выданы и подтверждены."
            f"{last}\n\n"
            "Что можно сделать дальше прямо во ВКонтакте:\n"
            "• нажать «📊 Прогресс» или отправить progress — посмотреть состояние;\n"
            "• нажать «🧾 История» или отправить history — посмотреть историю аудио;\n"
            "• отправить оценку от −10 до +10, если нужно зафиксировать состояние после последнего прослушивания;\n"
            "• когда появятся новые практики, нажать «🎧 Получить аудио».\n\n"
            "Telegram для этого не нужен — сценарий остаётся внутри ВКонтакте."
        )
    if platform == MessengerPlatform.MAX.value:
        return (
            "✅ Все доступные аудио в общей очереди уже выданы и подтверждены."
            f"{last}\n\n"
            "Дальше можно отправить progress для прогресса, history для истории или оценку от −10 до +10. "
            "Сценарий остаётся внутри MAX."
        )
    return (
        "✅ Все доступные аудио в общей очереди уже выданы и подтверждены."
        f"{last}\n\n"
        "Можно открыть прогресс, историю или отправить оценку состояния от −10 до +10."
    )


def _vk_post_audio_keyboard_json() -> str:
    def button(label: str, command: str, color: str = "secondary") -> dict[str, Any]:
        return {
            "action": {
                "type": "text",
                "label": label,
                "payload": json.dumps({"command": command}, ensure_ascii=False),
            },
            "color": color,
        }

    rows: list[list[dict[str, Any]]] = [
        [button("✅ Прослушал", "done", "positive")],
        [button("📊 Прогресс", "progress", "primary"), button("🧾 История", "history", "secondary")],
        [button("⬅️ Меню", "start", "secondary")],
    ]
    return json.dumps(
        {"one_time": False, "inline": False, "buttons": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _post_audio_control_kwargs(platform: str) -> dict[str, Any]:
    if platform == MessengerPlatform.VK.value:
        return {"keyboard_json": _vk_post_audio_keyboard_json()}
    return {}


def post_audio_control_kwargs(platform: str) -> dict[str, Any]:
    return _post_audio_control_kwargs(platform)


def _pending_caption(platform: str, item: AudioProgressItem, *, replay: bool = False) -> str:
    prefix = "Повторно отправил файл" if replay else "Отправил файл"
    return (
        f"🎧 Аудио №{item.anchor}: {item.title}\n\n"
        f"{prefix} прямо в {_platform_name(platform)}.\n"
        "Когда дослушаете, нажмите «✅ Прослушал» или отправьте done / готово / прослушал."
    )


def _post_audio_controls_text(platform: str, item: AudioProgressItem, *, replay: bool = False) -> str:
    head = (
        f"✅ Повторно отправил аудио №{item.anchor} — {item.title} прямо в {_platform_name(platform)}."
        if replay
        else f"✅ Аудио №{item.anchor} — {item.title} отправлено прямо в {_platform_name(platform)}."
    )
    return (
        f"{head}\n\n"
        "Когда прослушаете — нажмите кнопку «✅ Прослушал» ниже "
        "или отправьте done / готово / прослушал.\n\n"
        "После этого я покажу шкалу состояния от −10 до +10, как в Telegram.\n\n"
        "Для проверки результата можно нажать «📊 Прогресс» или «🧾 История». "
        "Telegram для этого не нужен — этот сценарий исполняется внутри текущего мессенджера."
    )


def post_audio_controls_text(platform: str, item: AudioProgressItem, *, replay: bool = False) -> str:
    return _post_audio_controls_text(platform, item, replay=replay)


def _replay_item_for_finished_queue(platform: str, snapshot: Any) -> AudioProgressItem | None:
    if platform not in {MessengerPlatform.VK.value, MessengerPlatform.MAX.value}:
        return None
    last_anchor = getattr(snapshot, "last_anchor", None)
    if last_anchor is None:
        return None
    try:
        return get_audio_item_by_anchor(int(last_anchor))
    except (TypeError, ValueError):
        return None


def _explicit_replay_item(snapshot: Any, *, anchor: int | None = None) -> AudioProgressItem | None:
    if anchor is not None:
        try:
            explicit = get_audio_item_by_anchor(int(anchor))
        except (TypeError, ValueError):
            explicit = None
        if explicit is not None:
            return explicit

    pending = getattr(snapshot, "pending_item", None)
    if pending is not None:
        return pending

    last_anchor = getattr(snapshot, "last_anchor", None)
    if last_anchor is None:
        return None
    try:
        return get_audio_item_by_anchor(int(last_anchor))
    except (TypeError, ValueError):
        return None


def _vk_file_is_native_audio(file_path: Any) -> bool:
    return getattr(file_path, "suffix", "").lower() in {".opus", ".ogg"}


async def _prepare_native_audio_path(platform: str, item: AudioProgressItem) -> Any:
    if platform == MessengerPlatform.MAX.value:
        return await asyncio.to_thread(ensure_max_opus_file, item.path)
    if platform == MessengerPlatform.VK.value:
        if _vk_file_is_native_audio(item.path):
            return item.path
        return await asyncio.to_thread(ensure_vk_opus_file, item.path)
    return item.path


def _vk_audio_access_link_text(item: AudioProgressItem, url: str, *, replay: bool = False) -> str:
    head = f"🎧 Повтор аудио №{item.anchor}: {item.title}" if replay else f"🎧 Аудио №{item.anchor}: {item.title}"
    return (
        f"{head}\n\n"
        "ВКонтакте не принял этот аудиофайл как вложение, поэтому даю безопасную ссылку на прослушивание:\n"
        f"{url}\n\n"
        "Откройте ссылку, прослушайте аудио, затем вернитесь сюда и нажмите «✅ Прослушал» "
        "или отправьте done / готово / прослушал.\n\n"
        "После этого я покажу шкалу состояния от −10 до +10, как в Telegram."
    )


async def _send_vk_audio_access_link(
    *,
    user_id: int,
    external_user_id: str,
    sender: Any,
    item: AudioProgressItem,
    replay: bool = False,
    sequence_key: str = "full_series",
) -> AudioDeliveryResult:
    base = (getattr(settings, "MESSENGER_PUBLIC_BASE_URL", "") or "").strip()
    if not base:
        raise UnsupportedMessengerDelivery(
            "VK audio access URL cannot be built: MESSENGER_PUBLIC_BASE_URL is empty"
        )

    token = issue_or_reuse_audio_access_token(
        int(user_id), item=item, platform=MessengerPlatform.VK.value, sequence_key=sequence_key
    )
    url = build_audio_access_url(token)
    if not url:
        raise UnsupportedMessengerDelivery("VK audio access URL cannot be built")

    await sender.send_text(
        external_user_id,
        _vk_audio_access_link_text(item, url, replay=replay),
        **_post_audio_control_kwargs(MessengerPlatform.VK.value),
    )
    log_audio_timeline_event(
        int(user_id),
        event_type="vk_audio_access_link_replayed" if replay else "vk_audio_access_link_sent",
        sequence_key=sequence_key,
        anchor=int(item.anchor),
        title=item.title,
        platform=MessengerPlatform.VK.value,
        token=token,
    )
    return AudioDeliveryResult(
        user_id=int(user_id),
        platform=MessengerPlatform.VK.value,
        item=item,
        transport="vk_audio_access_link_replay" if replay else "vk_audio_access_link_pending",
        message=(
            f"🎧 Дал ссылку на повтор аудио во ВКонтакте: №{item.anchor} — {item.title}.\n\n"
            if replay
            else f"🎧 Дал ссылку на аудио во ВКонтакте: №{item.anchor} — {item.title}.\n\n"
        ) + "Когда дослушаете, напишите: done / готово / прослушал.",
    )


async def send_vk_audio_access_link(
    *,
    user_id: int,
    external_user_id: str,
    sender: Any,
    item: AudioProgressItem,
    replay: bool = False,
    sequence_key: str = "full_series",
) -> AudioDeliveryResult:
    return await _send_vk_audio_access_link(
        user_id=int(user_id),
        external_user_id=external_user_id,
        sender=sender,
        item=item,
        replay=replay,
        sequence_key=sequence_key,
    )


async def _send_non_telegram_native(
    *,
    user_id: int,
    platform: str,
    external_user_id: str,
    sender: Any,
    item: AudioProgressItem,
    pending: AudioProgressItem | None,
    replay: bool = False,
) -> AudioDeliveryResult | None:
    if platform not in {MessengerPlatform.MAX.value, MessengerPlatform.VK.value}:
        return None
    try:
        audio_path = await _prepare_native_audio_path(platform, item)
        await sender.send_audio_file(
            external_user_id,
            audio_path,
            caption=_pending_caption(platform, item, replay=replay),
            **_post_audio_control_kwargs(platform),
        )
    except (
        AttributeError,
        RuntimeError,
        ValueError,
        TypeError,
        OSError,
        UnsupportedMessengerDelivery,
        MessengerTransportError,
    ) as exc:  # validator: allow-wide-except
        log_audio_timeline_event(
            int(user_id),
            event_type="native_audio_failed",
            sequence_key="full_series",
            anchor=int(item.anchor),
            title=item.title,
            platform=platform,
            meta_json=_native_audio_failure_meta(exc),
        )
        if platform == MessengerPlatform.VK.value:
            return await _send_vk_audio_access_link(
                user_id=int(user_id),
                external_user_id=external_user_id,
                sender=sender,
                item=item,
                replay=replay,
            )
        raise UnsupportedMessengerDelivery(NATIVE_AUDIO_REQUIRED_MESSAGE) from exc

    if pending is None:
        mark_pending_audio_delivery(int(user_id), item=item, platform=platform, token=None)
    log_audio_timeline_event(
        int(user_id),
        event_type="native_audio_replayed" if replay else "native_audio_sent",
        sequence_key="full_series",
        anchor=int(item.anchor),
        title=item.title,
        platform=platform,
    )

    try:
        await sender.send_text(
            external_user_id,
            _post_audio_controls_text(platform, item, replay=replay),
            **_post_audio_control_kwargs(platform),
        )
    except (
        AttributeError,
        RuntimeError,
        ValueError,
        TypeError,
        OSError,
        UnsupportedMessengerDelivery,
        MessengerTransportError,
    ) as exc:  # validator: allow-wide-except
        log_audio_timeline_event(
            int(user_id),
            event_type="post_audio_notice_failed",
            sequence_key="full_series",
            anchor=int(item.anchor),
            title=item.title,
            platform=platform,
            meta_json=_native_audio_failure_meta(exc),
        )

    return AudioDeliveryResult(
        user_id=int(user_id),
        platform=platform,
        item=item,
        transport=f"{platform}_native_audio_replay" if replay else f"{platform}_native_audio_pending",
        message=(
            f"🎧 Повторно отправил аудио в {_platform_name(platform)}: №{item.anchor} — {item.title}.\n\n"
            if replay
            else f"🎧 Отправил аудио в {_platform_name(platform)}: №{item.anchor} — {item.title}.\n\n"
        ) + "Когда дослушаете, напишите: done / готово / прослушал.",
    )


def _reserve_new_delivery(user_id: int, item: AudioProgressItem) -> PracticeAccessDecision:
    return check_and_reserve_for_audio(
        int(user_id), is_demo=False, session_id=None, audio_anchor=int(item.anchor)
    )


def _finish_delivery_access(decision: PracticeAccessDecision | None, *, delivered: bool) -> None:
    if decision is None:
        return
    finalize_audio_access(decision, delivered=delivered)


async def send_next_audio_to_user(
    user_id: int,
    *,
    senders: SenderRegistry,
    telegram_bot: Any | None = None,
    fallback: str = MessengerPlatform.TELEGRAM.value,
    target_platform: str | None = None,
) -> AudioDeliveryResult:
    """Send current pending audio or one new paid audio under wallet control."""

    uid = int(user_id)
    plan = build_delivery_plan(uid, fallback=fallback, preferred_platform=target_platform)
    snapshot = get_progress_snapshot(uid)
    pending = snapshot.pending_item
    replay = False

    if pending:
        item = pending
    else:
        item = get_next_audio_item(uid)
        if item is None:
            item = _replay_item_for_finished_queue(plan.platform, snapshot)
            replay = item is not None

    if item is None:
        return AudioDeliveryResult(
            user_id=uid,
            platform=plan.platform,
            item=None,
            transport="none",
            message=_queue_finished_message(plan.platform, snapshot),
        )

    access_decision: PracticeAccessDecision | None = None
    new_paid_delivery = pending is None and not replay
    if new_paid_delivery:
        access_decision = _reserve_new_delivery(uid, item)
        if not access_decision.allowed:
            return AudioDeliveryResult(
                user_id=uid,
                platform=plan.platform,
                item=None,
                transport="none",
                message=access_decision.message or "🔐 Для следующего аудио нужна доступная практика.",
            )

    try:
        if plan.platform == MessengerPlatform.TELEGRAM.value:
            if telegram_bot is None:
                raise UnsupportedMessengerDelivery(
                    "Telegram bot instance is required for telegram audio delivery"
                )
            if not plan.external_user_id:
                raise UnsupportedMessengerDelivery(f"No Telegram external id for user_id={uid}")
            await _send_telegram_audio(telegram_bot, plan.external_user_id, item)
            if pending is None and not replay:
                mark_pending_audio_delivery(uid, item=item, platform=plan.platform, token=None)
            log_audio_timeline_event(
                uid,
                event_type="telegram_audio_replayed" if replay else "telegram_sent",
                sequence_key="full_series",
                anchor=int(item.anchor),
                title=item.title,
                platform=plan.platform,
            )
            result = AudioDeliveryResult(
                user_id=uid,
                platform=plan.platform,
                item=item,
                transport="telegram_audio_replay" if replay else "telegram_audio_pending",
                message=(
                    f"🎧 Повторно отправил аудио: №{item.anchor} — {item.title}."
                    if replay
                    else f"🎧 Отправил аудио: №{item.anchor} — {item.title}.\n\n"
                    "Когда дослушаете, напишите: done / готово / прослушал."
                ),
            )
        else:
            sender = senders.get(plan.platform)
            if sender is None:
                raise UnsupportedMessengerDelivery(f"No sender registered for platform={plan.platform}")
            if not plan.external_user_id:
                raise UnsupportedMessengerDelivery(
                    f"No external user id for user_id={uid}, platform={plan.platform}"
                )

            native_result = await _send_non_telegram_native(
                user_id=uid,
                platform=plan.platform,
                external_user_id=plan.external_user_id,
                sender=sender,
                item=item,
                pending=pending if not replay else (pending or item),
                replay=replay,
            )
            if native_result is None:
                raise UnsupportedMessengerDelivery(NATIVE_AUDIO_REQUIRED_MESSAGE)
            result = native_result
    except (
        AttributeError,
        RuntimeError,
        ValueError,
        TypeError,
        OSError,
        UnsupportedMessengerDelivery,
        MessengerTransportError,
    ):  # validator: allow-wide-except
        _finish_delivery_access(access_decision, delivered=False)
        raise

    _finish_delivery_access(access_decision, delivered=True)
    if access_decision is not None and access_decision.warning:
        result = AudioDeliveryResult(
            user_id=result.user_id,
            platform=result.platform,
            item=result.item,
            transport=result.transport,
            message=f"{access_decision.warning}\n\n{result.message}",
        )
    return result


async def send_replay_audio_to_user(
    user_id: int,
    *,
    senders: SenderRegistry,
    telegram_bot: Any | None = None,
    fallback: str = MessengerPlatform.TELEGRAM.value,
    target_platform: str | None = None,
    anchor: int | None = None,
) -> AudioDeliveryResult:
    """Replay already issued/confirmed audio without advancing or charging."""

    uid = int(user_id)
    plan = build_delivery_plan(uid, fallback=fallback, preferred_platform=target_platform)
    snapshot = get_progress_snapshot(uid)
    item = _explicit_replay_item(snapshot, anchor=anchor)

    if item is None:
        return AudioDeliveryResult(
            user_id=uid,
            platform=plan.platform,
            item=None,
            transport="none",
            message=(
                "Пока нет аудио для повтора. "
                "Нажмите «🌿 Попробовать бесплатно» или отправьте continue, чтобы начать маршрут."
            ),
        )

    if plan.platform == MessengerPlatform.TELEGRAM.value:
        if telegram_bot is None or not plan.external_user_id:
            raise UnsupportedMessengerDelivery(
                "Telegram replay requires bot instance and external Telegram id"
            )
        await _send_telegram_audio(telegram_bot, plan.external_user_id, item)
        log_audio_timeline_event(
            uid,
            event_type="telegram_audio_replayed",
            sequence_key="full_series",
            anchor=int(item.anchor),
            title=item.title,
            platform=plan.platform,
        )
        return AudioDeliveryResult(
            user_id=uid,
            platform=plan.platform,
            item=item,
            transport="telegram_audio_replay",
            message=f"🎧 Повторно отправил аудио: №{item.anchor} — {item.title}.",
        )

    sender = senders.get(plan.platform)
    if sender is None:
        raise UnsupportedMessengerDelivery(f"No sender registered for platform={plan.platform}")
    if not plan.external_user_id:
        raise UnsupportedMessengerDelivery(
            f"No external user id for user_id={uid}, platform={plan.platform}"
        )

    pending_marker = snapshot.pending_item or item
    native_result = await _send_non_telegram_native(
        user_id=uid,
        platform=plan.platform,
        external_user_id=plan.external_user_id,
        sender=sender,
        item=item,
        pending=pending_marker,
        replay=True,
    )
    if native_result is not None:
        return native_result

    raise UnsupportedMessengerDelivery(NATIVE_AUDIO_REQUIRED_MESSAGE)
