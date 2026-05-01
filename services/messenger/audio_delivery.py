from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from services.messenger.outbound import SenderRegistry, build_delivery_plan, UnsupportedMessengerDelivery
from services.messenger.platforms import MessengerPlatform
from services.messenger.audio_links import build_audio_access_url
from services.messenger.audio_progress import (
    get_progress_snapshot,
    get_next_audio_item,
    get_audio_item_by_anchor,
    mark_pending_audio_delivery,
    AudioProgressItem,
)
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


def _score_scale_text() -> str:
    return (
        'Шкала оценки после прослушивания:\n'
        '−10 — стало сильно хуже, 0 — без изменений, +10 — стало сильно лучше.\n'
        'Можно отправить любое число от −10 до +10, например: −2, 0, 4 или 8.'
    )


def _queue_finished_message(platform: str, snapshot: Any) -> str:
    last = ''
    if getattr(snapshot, 'last_anchor', None):
        last_title = getattr(snapshot, 'last_title', '') or 'последнее аудио'
        last = f'\n\nПоследний подтверждённый трек: №{snapshot.last_anchor} — {last_title}.'

    if platform == MessengerPlatform.VK.value:
        return (
            '✅ Все доступные аудио в общей очереди уже выданы и подтверждены.'
            f'{last}\n\n'
            'Что можно сделать дальше прямо во ВКонтакте:\n'
            '• нажать «📊 Прогресс» или отправить progress — посмотреть состояние;\n'
            '• нажать «🧾 История» или отправить history — посмотреть историю аудио;\n'
            '• отправить оценку от −10 до +10, если нужно зафиксировать состояние после последнего прослушивания;\n'
            '• когда появятся новые практики, нажать «🎧 Получить аудио».\n\n'
            'Telegram для этого не нужен — сценарий остаётся внутри ВКонтакте.'
        )

    if platform == MessengerPlatform.MAX.value:
        return (
            '✅ Все доступные аудио в общей очереди уже выданы и подтверждены.'
            f'{last}\n\n'
            'Дальше можно отправить progress для прогресса, history для истории или оценку от −10 до +10. '
            'Сценарий остаётся внутри MAX.'
        )

    return (
        '✅ Все доступные аудио в общей очереди уже выданы и подтверждены.'
        f'{last}\n\n'
        'Можно открыть прогресс, историю или отправить оценку состояния от −10 до +10.'
    )


def _vk_post_audio_keyboard_json() -> str:
    """VK post-audio controls with score-scale parity to Telegram.

    After native VK audio delivery the user must not be dropped back to the
    main menu. The active keyboard is the post-audio surface: done/progress and
    the full -10..+10 score scale. Numeric buttons intentionally send plain text
    values because the canonical text parser already validates and stores scores.
    """

    def button(label: str, command: str, color: str = 'secondary') -> dict[str, Any]:
        return {
            'action': {
                'type': 'text',
                'label': label,
                'payload': json.dumps({'command': command}, ensure_ascii=False),
            },
            'color': color,
        }

    rows: list[list[dict[str, Any]]] = [
        [
            button('✅ Прослушал', 'done', 'positive'),
            button('📊 Прогресс', 'progress', 'primary'),
        ],
        [
            button('🧾 История', 'history', 'secondary'),
            button('🎧 Получить аудио', 'continue', 'secondary'),
        ],
    ]

    for row in [
        [-10, -9, -8],
        [-7, -6, -5],
        [-4, -3, -2],
        [-1, 0, 1],
        [2, 3, 4],
        [5, 6, 7],
        [8, 9, 10],
    ]:
        rows.append([
            button(
                ('+' if value > 0 else '') + str(value),
                str(value),
                'primary' if value == 0 else 'secondary',
            )
            for value in row
        ])

    rows.append([
        button('⬅️ Меню', 'start', 'secondary'),
    ])

    return json.dumps(
        {
            'one_time': False,
            'inline': False,
            'buttons': rows,
        },
        ensure_ascii=False,
        separators=(',', ':'),
    )


def _post_audio_control_kwargs(platform: str) -> dict[str, Any]:
    if platform == MessengerPlatform.VK.value:
        return {'keyboard_json': _vk_post_audio_keyboard_json()}
    return {}


def _pending_caption(platform: str, item: AudioProgressItem, *, replay: bool = False) -> str:
    prefix = 'Повторно отправил файл' if replay else 'Отправил файл'
    return (
        f'🎧 Аудио №{item.anchor}: {item.title}\n\n'
        f'{prefix} прямо в {_platform_name(platform)}.\n'
        'Когда дослушаете, нажмите «✅ Прослушал» или отправьте done / готово / прослушал.\n'
        'После этого отправьте оценку от -10 до 10.'
    )


def _post_audio_controls_text(platform: str, item: AudioProgressItem, *, replay: bool = False) -> str:
    head = (
        f'✅ Повторно отправил аудио №{item.anchor} — {item.title} прямо в {_platform_name(platform)}.'
        if replay else
        f'✅ Аудио №{item.anchor} — {item.title} отправлено прямо в {_platform_name(platform)}.'
    )
    return (
        f'{head}\n\n'
        'Что делать дальше:\n'
        '1. Дослушайте аудио.\n'
        '2. Нажмите «✅ Прослушал» или отправьте done / готово / прослушал.\n'
        '3. Затем отправьте оценку состояния.\n\n'
        f'{_score_scale_text()}\n\n'
        'Ниже есть кнопки оценки от −10 до +10 — можно нажать число сразу после прослушивания.\n\n'
        'Для проверки результата можно нажать «📊 Прогресс» или «🧾 История». '
        'Telegram для этого не нужен — этот сценарий исполняется внутри текущего мессенджера.'
    )


def _replay_item_for_finished_queue(platform: str, snapshot: Any) -> AudioProgressItem | None:
    """Return the last confirmed audio for messenger replay.

    The queue pointer means “next new audio”. Once the user has confirmed the
    last available track, there is no next item. VK/MAX users still need a
    platform-native way to listen again, so replay intentionally reuses the last
    confirmed anchor without resetting or advancing progress.
    """
    if platform not in {MessengerPlatform.VK.value, MessengerPlatform.MAX.value}:
        return None
    last_anchor = getattr(snapshot, 'last_anchor', None)
    if last_anchor is None:
        return None
    try:
        return get_audio_item_by_anchor(int(last_anchor))
    except (TypeError, ValueError):
        return None


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
        await sender.send_audio_file(
            external_user_id,
            item.path,
            caption=_pending_caption(platform, item, replay=replay),
            **_post_audio_control_kwargs(platform),
        )
    except (RuntimeError, ValueError, TypeError, OSError, UnsupportedMessengerDelivery):
        log_audio_timeline_event(int(user_id), event_type="native_audio_fallback", sequence_key="full_series", anchor=int(item.anchor), title=item.title, platform=platform)
        return None
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

    await sender.send_text(
        external_user_id,
        _post_audio_controls_text(platform, item, replay=replay),
        **_post_audio_control_kwargs(platform),
    )

    return AudioDeliveryResult(
        user_id=int(user_id),
        platform=platform,
        item=item,
        transport=f'{platform}_native_audio_replay' if replay else f'{platform}_native_audio_pending',
        message=(
            f'🎧 Повторно отправил аудио в {_platform_name(platform)}: №{item.anchor} — {item.title}.\n\n'
            if replay else
            f'🎧 Отправил аудио в {_platform_name(platform)}: №{item.anchor} — {item.title}.\n\n'
        ) + 'Когда дослушаете, напишите: done / готово / прослушал.',
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
    replay = False
    if pending:
        item = pending
    else:
        item = get_next_audio_item(int(user_id))
        if item is None:
            item = _replay_item_for_finished_queue(plan.platform, snapshot)
            replay = item is not None
    if item is None:
        return AudioDeliveryResult(
            user_id=int(user_id),
            platform=plan.platform,
            item=None,
            transport='none',
            message=_queue_finished_message(plan.platform, snapshot),
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
        replay=replay,
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
        f'🎧 {"Повтор аудио" if replay else "Следующее аудио по вашей общей очереди"}: №{item.anchor} — {item.title}\n\n'
        f'Слушать: {public_url}\n\n'
        f'Для {_platform_name(plan.platform)} это аварийная ссылка на файл: native-отправка сейчас не прошла.\n'
        'После прослушивания нажмите «✅ Прослушал» или отправьте done / готово / прослушал.\n'
        'Затем отправьте оценку от -10 до 10 — например: -2, 0, 4 или 8.'
    )
    await sender.send_text(
        plan.external_user_id,
        text,
        disable_link_preview=False,
        **_post_audio_control_kwargs(plan.platform),
    )
    if pending is None:
        mark_pending_audio_delivery(int(user_id), item=item, platform=plan.platform, token=access_token)
    log_audio_timeline_event(
        int(user_id),
        event_type="link_replayed" if replay else "link_sent",
        sequence_key="full_series",
        anchor=int(item.anchor),
        title=item.title,
        platform=plan.platform,
        token=access_token,
    )
    return AudioDeliveryResult(
        user_id=int(user_id),
        platform=plan.platform,
        item=item,
        transport='messenger_link_replay' if replay else 'messenger_link',
        message=(
            f'🎧 Отправил аварийную ссылку на повтор в {_platform_name(plan.platform)}: №{item.anchor} — {item.title}'
            if replay else
            f'🎧 Отправил аварийную ссылку в {_platform_name(plan.platform)}: №{item.anchor} — {item.title}'
        ),
    )
