from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message

from runtime.messenger_senders import TelegramBotSender, MaxBotSender, VkBotSender
from services.messenger.audio_delivery import send_next_audio_to_user
from services.messenger.audio_progress import get_progress_snapshot, SEQUENCE_FULL_SERIES, confirm_pending_audio_delivery
from services.messenger.timeline import get_recent_audio_timeline
from services.messenger.bridge import issue_bridge_token
from services.messenger.links import build_switch_targets
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery
from services.messenger.platforms import platform_title

router = Router()


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


def _registry(message: Message) -> SenderRegistry:
    return SenderRegistry(
        telegram=TelegramBotSender(message.bot),
        max=MaxBotSender(),
        vk=VkBotSender(),
    )


@router.message(F.text.in_({'/continue', 'continue', '/next', 'next', '/audio', 'audio', 'следующее аудио'}))
async def continue_audio(message: Message) -> None:
    uid = _message_user_id(message)
    if uid is None:
        return
    try:
        result = await send_next_audio_to_user(uid, senders=_registry(message), telegram_bot=message.bot)
        if result.platform != 'telegram':
            await message.answer(result.message)
    except UnsupportedMessengerDelivery:
        await message.answer(
            '⚠️ Не удалось отправить следующее аудио в выбранный мессенджер. '
            'Для MAX/ВКонтакте нужен публичный адрес MESSENGER_PUBLIC_BASE_URL, '
            'а для привязки каналов — сначала откройте switch-ссылку в нужном мессенджере.'
        )



@router.message(F.text.in_({'/done', 'done', 'готово', 'прослушал', 'дослушал'}))
async def confirm_audio(message: Message) -> None:
    uid = _message_user_id(message)
    if uid is None:
        return
    confirmed = confirm_pending_audio_delivery(uid, platform='telegram')
    if confirmed is None:
        await message.answer('ℹ️ Сейчас нет аудио, ожидающего подтверждения. Отправьте /continue, чтобы получить текущее или следующее аудио.')
        return
    await message.answer(f'✅ Подтвердил аудио №{confirmed.anchor} — {confirmed.title}. Отправляю дальше.')
    try:
        result = await send_next_audio_to_user(uid, senders=_registry(message), telegram_bot=message.bot, target_platform='telegram', fallback='telegram')
        if result.platform != 'telegram':
            await message.answer(result.message)
    except UnsupportedMessengerDelivery:
        await message.answer('⚠️ Не удалось отправить следующее аудио после подтверждения.')

@router.message(F.text.in_({'/progress', 'progress', 'прогресс', 'где остановился'}))
async def audio_progress(message: Message) -> None:
    uid = _message_user_id(message)
    if uid is None:
        return
    snap = get_progress_snapshot(uid)
    pending_tail = ''
    if snap.pending_item is not None:
        pending_tail = (
            f'\n\n⏳ Уже выдано, но ещё не подтверждено открытием: №{snap.pending_item.anchor} — {snap.pending_item.title} '
            f'({platform_title(snap.pending_platform)}).'
        )
    if snap.last_anchor is None:
        if snap.next_item is None:
            await message.answer('🎧 Аудиосерия пока не найдена в каталоге.')
            return
        await message.answer(
            f'🎧 Вы ещё не запускали общую очередь. Следующим будет №{snap.next_item.anchor} — {snap.next_item.title}.' + pending_tail
        )
        return
    next_text = f'Следующим будет №{snap.next_item.anchor} — {snap.next_item.title}.' if snap.next_item else 'Серия уже дослушана до конца.'
    await message.answer(
        '🎧 Общий прогресс аудио\n\n'
        f'Последнее подтверждённое аудио: №{snap.last_anchor} — {snap.last_title}\n'
        f'Подтверждено в канале: {platform_title(snap.last_platform)}\n\n'
        f'{next_text}{pending_tail}'
    )


@router.message(F.text.in_({'/history', 'history', '/timeline', 'timeline', 'история'}))
async def audio_history(message: Message) -> None:
    uid = _message_user_id(message)
    if uid is None:
        return
    events = get_recent_audio_timeline(uid, sequence_key=SEQUENCE_FULL_SERIES, limit=8)
    if not events:
        await message.answer('🧾 История аудио и переходов пока пуста.')
        return
    labels = {
        'bridge_linked': 'перешёл в другой мессенджер',
        'issued_pending': 'выдано следующее аудио',
        'reused_pending': 'повторно показано уже выданное аудио',
        'link_sent': 'отправлена ссылка на аудио',
        'access_confirmed': 'аудио открыто и подтверждено',
        'confirmed_delivery': 'аудио подтверждено доставкой',
        'telegram_sent': 'аудио отправлено в Telegram',
        'native_audio_sent': 'аудио отправлено как вложение',
        'native_audio_fallback': 'native-вложение недоступно, использована ссылка',
        'manual_confirmed': 'аудио подтверждено вручную',
    }
    lines = ['🧾 Последние шаги по общей аудио-очереди:', '']
    for event in events:
        line = f"• {event.created_at}: {labels.get(event.event_type, event.event_type)}"
        if event.anchor is not None:
            line += f" — №{event.anchor}"
        if event.title:
            line += f" — {event.title}"
        if event.platform:
            line += f" ({platform_title(event.platform)})"
        lines.append(line)
    await message.answer('\n'.join(lines))


@router.message(F.text.in_({'/switch', 'switch', 'другой мессенджер', 'сменить канал'}))
async def switch_channel(message: Message) -> None:
    uid = _message_user_id(message)
    if uid is None:
        return
    token = issue_bridge_token(uid)
    targets = build_switch_targets(token)
    if not targets:
        await message.answer('🔁 Ссылки переключения пока не настроены. Нужно задать TELEGRAM_BOT_USERNAME, MAX_BOT_LINK_BASE/MAX_BOT_NAME и VK_GROUP_ID.')
        return
    lines = ['🔁 Откройте один из этих мессенджеров — и он привяжется к вашему текущему профилю:','']
    for item in targets:
        lines.append(f"• {item['title']}: {item['url']}")
    lines.append('')
    lines.append('После входа команда /continue пришлёт текущее или следующее аудио общей очереди.')
    await message.answer('\n'.join(lines), disable_web_page_preview=True)
