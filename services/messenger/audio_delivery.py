from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.messenger.outbound import SenderRegistry, build_delivery_plan, UnsupportedMessengerDelivery
from services.messenger.platforms import MessengerPlatform
from services.messenger.audio_links import build_audio_access_url
from services.messenger.audio_progress import get_progress_snapshot, get_next_audio_item, mark_pending_audio_delivery, AudioProgressItem
from services.messenger.audio_access import issue_or_reuse_audio_access_token
from services.messenger.timeline import log_audio_timeline_event


async def _send_telegram_audio(bot: Any, external_user_id: str, item: AudioProgressItem) -> Any:
    from services.fast_send_audio import send_audio_cached

    return await send_audio_cached(
        bot,
        int(external_user_id),
        key=f'cross_audio:{item.path.name}',
        file_path=item.path,
        caption=f'🎧 Аудио №{item.anchor}: {item.title}',
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
        return 'ВКонтакте'
    if platform == MessengerPlatform.MAX.value:
        return 'MAX'
    if platform == MessengerPlatform.TELEGRAM.value:
        return 'Telegram'
    return platform


def _pending_caption(platform: str, item: AudioProgressItem) -> str:
    return (
        f'🎧 Аудио №{item.anchor}: {item.title}\n\n'
        f'Отправил файл прямо в {_platform_name(platform)}.\n'
        'Когда дослушаете, нажмите «✅ Прослушал» или отправьте done / готово / прослушал.\n'
        'После этого отправьте оценку от -10 до 10.'
    )


async def _send_non_telegram_native(
    *,
    user_id: int,
    platform: str,
    external_user_id: str,
    sender: Any,
    item: AudioProgressItem,
    pending: AudioProgressItem | None,
) -> AudioDeliveryResult | None:
    if platform not in {MessengerPlatform.MAX.value, MessengerPlatform.VK.value}:
        return None
    try:
        await sender.send_audio_file(
            external_user_id,
            item.path,
            caption=_pending_caption(platform, item),
        )
    except (RuntimeError, ValueError, TypeError, OSError, UnsupportedMessengerDelivery):
        log_audio_timeline_event(int(user_id), event_type="native_audio_fallback", sequence_key="full_series", anchor=int(item.anchor), title=item.title, platform=platform)
        return None
    if pending is None:
        mark_pending_audio_delivery(int(user_id), item=item, platform=platform, token=None)
    log_audio_timeline_event(int(user_id), event_type="native_audio_sent", sequence_key="full_series", anchor=int(item.anchor), title=item.title, platform=platform)
    return AudioDeliveryResult(
        user_id=int(user_id),
        platform=platform,
        item=item,
        transport=f'{platform}_native_audio_pending',
        message=(
            f'🎧 Отправил аудио в {_platform_name(platform)}: №{item.anchor} — {item.title}.\n\n'
            'Когда дослушаете, напишите: done / готово / прослушал.'
        ),
    )


async def send_next_audio_to_user(
    user_id: int,
    *,
    senders: SenderRegistry,
    telegram_bot: Any | None = None,
    fallback: str = MessengerPlatform.TELEGRAM.value,
    target_platform: str | None = None,
) -> AudioDeliveryResult:
    plan = build_delivery_plan(int(user_id), fallback=fallback, preferred_platform=target_platform)
    snapshot = get_progress_snapshot(int(user_id))
    pending = snapshot.pending_item
    if pending:
        item = pending
    else:
        item = get_next_audio_item(int(user_id))
    if item is None:
        suffix = f" Последний трек: №{snapshot.last_anchor} — {snapshot.last_title}." if snapshot.last_anchor else ''
        return AudioDeliveryResult(
            user_id=int(user_id),
            platform=plan.platform,
            item=None,
            transport='none',
            message='✅ Серия уже дослушана целиком.' + suffix,
        )

    if plan.platform == MessengerPlatform.TELEGRAM.value:
        if telegram_bot is None:
            raise UnsupportedMessengerDelivery('Telegram bot instance is required for telegram audio delivery')
        if not plan.external_user_id:
            raise UnsupportedMessengerDelivery(f'No Telegram external id for user_id={user_id}')
        await _send_telegram_audio(telegram_bot, plan.external_user_id, item)
        if pending is None:
            mark_pending_audio_delivery(int(user_id), item=item, platform=plan.platform, token=None)
        log_audio_timeline_event(int(user_id), event_type="telegram_sent", sequence_key="full_series", anchor=int(item.anchor), title=item.title, platform=plan.platform)
        return AudioDeliveryResult(
            user_id=int(user_id),
            platform=plan.platform,
            item=item,
            transport='telegram_audio_pending',
            message=(
                f'🎧 Отправил аудио: №{item.anchor} — {item.title}.\n\n'
                'Когда дослушаете, напишите: done / готово / прослушал — и я сразу пришлю следующее.'
            ),
        )

    sender = senders.get(plan.platform)
    if sender is None:
        raise UnsupportedMessengerDelivery(f'No sender registered for platform={plan.platform}')
    if not plan.external_user_id:
        raise UnsupportedMessengerDelivery(f'No external user id for user_id={user_id}, platform={plan.platform}')

    native_result = await _send_non_telegram_native(
        user_id=int(user_id),
        platform=plan.platform,
        external_user_id=plan.external_user_id,
        sender=sender,
        item=item,
        pending=pending,
    )
    if native_result is not None:
        return native_result

    access_token = issue_or_reuse_audio_access_token(int(user_id), item=item, platform=plan.platform)
    public_url = build_audio_access_url(access_token)
    if not public_url:
        raise UnsupportedMessengerDelivery(
            'MESSENGER_PUBLIC_BASE_URL is empty; cannot deliver cross-messenger audio link for non-Telegram platforms'
        )
    text = (
        f'🎧 Следующее аудио по вашей общей очереди: №{item.anchor} — {item.title}\n\n'
        f'Слушать: {public_url}\n\n'
        f'Для {_platform_name(plan.platform)} это аварийная ссылка на файл: native-отправка сейчас не прошла.\n'
        'После прослушивания нажмите «✅ Прослушал» или отправьте done / готово / прослушал.\n'
        'Затем отправьте оценку от -10 до 10 — например: -2, 0, 4 или 8.'
    )
    await sender.send_text(plan.external_user_id, text, disable_link_preview=False)
    if pending is None:
        mark_pending_audio_delivery(int(user_id), item=item, platform=plan.platform, token=access_token)
    log_audio_timeline_event(int(user_id), event_type="link_sent", sequence_key="full_series", anchor=int(item.anchor), title=item.title, platform=plan.platform, token=access_token)
    return AudioDeliveryResult(
        user_id=int(user_id),
        platform=plan.platform,
        item=item,
        transport='messenger_link',
        message=f'🎧 Отправил аварийную ссылку в {_platform_name(plan.platform)}: №{item.anchor} — {item.title}',
    )
