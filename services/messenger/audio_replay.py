from __future__ import annotations

from typing import Any

from runtime.messenger_transport_errors import MessengerTransportError
from services.messenger.audio_delivery import (
    AudioDeliveryResult,
    _send_non_telegram_native,
    _send_telegram_audio,
)
from services.messenger.audio_progress import get_audio_item_by_anchor, get_progress_snapshot
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery, build_delivery_plan
from services.messenger.platforms import MessengerPlatform
from services.messenger.timeline import log_audio_timeline_event


def _replay_item_for_user(user_id: int, *, anchor: int | None = None) -> Any | None:
    """Return the exact audio item that should be replayed without advancing queue state."""
    snapshot = get_progress_snapshot(int(user_id))

    if anchor is not None:
        try:
            explicit = get_audio_item_by_anchor(int(anchor))
        except (TypeError, ValueError):
            explicit = None
        if explicit is not None:
            return explicit

    if snapshot.pending_item is not None:
        return snapshot.pending_item

    if snapshot.last_anchor is None:
        return None

    try:
        return get_audio_item_by_anchor(int(snapshot.last_anchor))
    except (TypeError, ValueError):
        return None


async def send_replay_audio_to_user(
    user_id: int,
    *,
    senders: SenderRegistry,
    telegram_bot: Any | None = None,
    fallback: str = MessengerPlatform.TELEGRAM.value,
    target_platform: str | None = None,
    anchor: int | None = None,
) -> AudioDeliveryResult:
    """Replay already issued/confirmed audio without selecting the next catalogue item.

    This is intentionally separate from ``send_next_audio_to_user``.  A user who
    presses "repeat" expects the same track again; advancing to the next anchor is
    a user-visible regression in VK/MAX, especially once the catalogue has more
    than one item.
    """

    uid = int(user_id)
    plan = build_delivery_plan(uid, fallback=fallback, preferred_platform=target_platform)
    snapshot = get_progress_snapshot(uid)
    item = _replay_item_for_user(uid, anchor=anchor)

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
            raise UnsupportedMessengerDelivery("Telegram replay requires bot instance and external Telegram id")
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
        raise UnsupportedMessengerDelivery(f"No external user id for user_id={uid}, platform={plan.platform}")

    # ``_send_non_telegram_native`` marks a delivery as pending only when the
    # ``pending`` argument is None.  For replay we deliberately pass a marker so
    # an already confirmed item is not converted back into a pending next step.
    pending_marker = snapshot.pending_item or item
    result = await _send_non_telegram_native(
        user_id=uid,
        platform=plan.platform,
        external_user_id=plan.external_user_id,
        sender=sender,
        item=item,
        pending=pending_marker,
        replay=True,
    )
    if result is None:
        raise MessengerTransportError(f"Replay is unsupported for platform={plan.platform}")
    return result
