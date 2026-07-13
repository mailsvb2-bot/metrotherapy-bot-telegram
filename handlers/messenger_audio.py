from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import Message

from keyboards.inline import kb_mood_scale
from runtime.messenger_senders import MaxBotSender, TelegramBotSender, VkBotSender
from services.messenger.audio_delivery import send_next_audio_to_user
from services.messenger.audio_progress import (
    SEQUENCE_FULL_SERIES,
    confirm_pending_audio_delivery,
    get_progress_snapshot,
)
from services.messenger.bridge import issue_bridge_token
from services.messenger.links import build_switch_targets
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery
from services.messenger.platforms import platform_title
from services.messenger.timeline import get_recent_audio_timeline
from services.mood import get_session
from services.mood_text_flow import find_pending_post_session_id
from services.payments.ui import kb_tariffs
from services.practice_journey import start_or_resume_paid_practice

router = Router()


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


def _registry(bot: Bot) -> SenderRegistry:
    return SenderRegistry(
        telegram=TelegramBotSender(bot),
        max=MaxBotSender(),
        vk=VkBotSender(),
    )


@router.message(F.text.in_({"/continue", "continue", "/next", "next", "/audio", "audio", "следующее аудио"}))
async def continue_audio(message: Message) -> None:
    uid = _message_user_id(message)
    bot = message.bot
    if uid is None or bot is None:
        return

    start = start_or_resume_paid_practice(uid)
    if start.ready_for_pre_score:
        await message.answer(
            start.message,
            reply_markup=kb_mood_scale(int(start.session_id), stage="pre"),
        )
        return

    if start.status == "insufficient_balance":
        await message.answer(start.message, reply_markup=kb_tariffs(int(uid)))
        return

    if start.status != "pending_audio":
        await message.answer(start.message)
        return

    try:
        result = await send_next_audio_to_user(
            uid,
            senders=_registry(bot),
            telegram_bot=bot,
            target_platform="telegram",
            fallback="telegram",
        )
        await message.answer(result.message)
    except UnsupportedMessengerDelivery:
        await message.answer("⚠️ Не удалось отправить текущее аудио. Попробуйте ещё раз позже.")


@router.message(F.text.in_({"/done", "done", "готово", "прослушал", "дослушал"}))
async def confirm_audio(message: Message) -> None:
    uid = _message_user_id(message)
    if uid is None:
        return

    session_id = find_pending_post_session_id(uid)
    session = get_session(session_id) if session_id is not None else None
    sequence_key = (
        "demo" if session is not None and str(session.source or "") == "demo" else SEQUENCE_FULL_SERIES
    )
    confirmed = confirm_pending_audio_delivery(uid, platform="telegram", sequence_key=sequence_key)

    if confirmed is None and session_id is None:
        await message.answer(
            "ℹ️ Сейчас нет аудио, ожидающего подтверждения. "
            "Отправьте /continue, чтобы продолжить маршрут."
        )
        return

    if session_id is None:
        await message.answer(
            f"✅ Подтвердил аудио №{confirmed.anchor} — {confirmed.title}.\n\n"
            "Чтобы продолжить маршрут, отправьте /continue."
        )
        return

    title = (
        f"аудио №{confirmed.anchor} — {confirmed.title}"
        if confirmed is not None
        else "текущее аудио"
    )
    await message.answer(
        f"✅ Подтвердил {title}.\n\n"
        "Теперь оцените состояние ПОСЛЕ прослушивания от −10 до +10.",
        reply_markup=kb_mood_scale(int(session_id), stage="post"),
    )


@router.message(F.text.in_({"/progress", "progress", "прогресс", "где остановился"}))
async def audio_progress(message: Message) -> None:
    uid = _message_user_id(message)
    if uid is None:
        return
    snap = get_progress_snapshot(uid)
    pending_tail = ""
    if snap.pending_item is not None:
        pending_tail = (
            f"\n\n⏳ Уже выдано, но ещё не подтверждено: "
            f"№{snap.pending_item.anchor} — {snap.pending_item.title} "
            f"({platform_title(snap.pending_platform)})."
        )
    if snap.last_anchor is None:
        if snap.next_item is None:
            await message.answer("🎧 Аудиосерия пока не найдена в каталоге.")
            return
        await message.answer(
            f"🎧 Вы ещё не запускали общий маршрут. "
            f"Следующей будет №{snap.next_item.anchor} — {snap.next_item.title}."
            + pending_tail
        )
        return
    next_text = (
        f"Следующей будет №{snap.next_item.anchor} — {snap.next_item.title}."
        if snap.next_item
        else "Серия уже дослушана до конца."
    )
    await message.answer(
        "🎧 Общий прогресс аудио\n\n"
        f"Последнее подтверждённое аудио: №{snap.last_anchor} — {snap.last_title}\n"
        f"Подтверждено в канале: {platform_title(snap.last_platform)}\n\n"
        f"{next_text}{pending_tail}"
    )


@router.message(F.text.in_({"/history", "history", "/timeline", "timeline", "история"}))
async def audio_history(message: Message) -> None:
    uid = _message_user_id(message)
    if uid is None:
        return
    events = get_recent_audio_timeline(uid, sequence_key=SEQUENCE_FULL_SERIES, limit=8)
    if not events:
        await message.answer("🧾 История аудио и переходов пока пуста.")
        return
    labels = {
        "bridge_linked": "перешёл в другой мессенджер",
        "issued_pending": "выдано следующее аудио",
        "reused_pending": "повторно показано уже выданное аудио",
        "link_sent": "отправлена ссылка на аудио",
        "access_confirmed": "аудио открыто и подтверждено",
        "confirmed_delivery": "аудио подтверждено доставкой",
        "telegram_sent": "аудио отправлено в Telegram",
        "native_audio_sent": "аудио отправлено как вложение",
        "native_audio_fallback": "native-вложение недоступно, использована ссылка",
        "manual_confirmed": "аудио подтверждено вручную",
    }
    lines = ["🧾 Последние шаги по общей аудио-очереди:", ""]
    for event in events:
        line = f"• {event.created_at}: {labels.get(event.event_type, event.event_type)}"
        if event.anchor is not None:
            line += f" — №{event.anchor}"
        if event.title:
            line += f" — {event.title}"
        if event.platform:
            line += f" ({platform_title(event.platform)})"
        lines.append(line)
    await message.answer("\n".join(lines))


@router.message(F.text.in_({"/switch", "switch", "другой мессенджер", "сменить канал"}))
async def switch_channel(message: Message) -> None:
    uid = _message_user_id(message)
    if uid is None:
        return
    token = issue_bridge_token(uid)
    targets = build_switch_targets(token)
    if not targets:
        await message.answer(
            "🔁 Ссылки переключения пока не настроены. "
            "Нужно задать TELEGRAM_BOT_USERNAME, MAX_BOT_LINK_BASE/MAX_BOT_NAME и VK_GROUP_ID."
        )
        return
    lines = [
        "🔁 Откройте один из этих мессенджеров — и он привяжется к Вашему текущему профилю:",
        "",
    ]
    for item in targets:
        lines.append(f"• {item['title']}: {item['url']}")
    lines.extend([
        "",
        "После входа команда /continue продолжит тот же маршрут и тот же баланс практик.",
    ])
    await message.answer("\n".join(lines), disable_web_page_preview=True)
