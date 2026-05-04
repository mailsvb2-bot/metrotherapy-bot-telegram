from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

from services.audio_anchor import get_by_anchor
from services.mood import get_session, set_pre, set_post, mark_audio_sent, last_delta
from services.subscription import register_touch
from services.progress import advance
from services.messenger.audio_progress import AudioProgressItem, mark_pending_audio_delivery, record_audio_delivery
from services.messenger.audio_access import issue_or_reuse_audio_access_token
from services.messenger.audio_links import build_audio_access_url
from services.messenger.outbound import SenderRegistry, build_delivery_plan, UnsupportedMessengerDelivery
from services.messenger.platforms import MessengerPlatform
from services.messenger.timeline import log_audio_timeline_event
from services.messenger.max_audio import ensure_max_opus_file
from services.events import log_event


def parse_score_text(text: str | None) -> int | None:
    raw = (text or '').strip().replace('−', '-')
    if not raw:
        return None
    if raw.startswith('/score '):
        raw = raw.split(maxsplit=1)[1].strip()
    if raw.startswith('score '):
        raw = raw.split(maxsplit=1)[1].strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if -10 <= value <= 10:
        return value
    return None


def find_pending_pre_session_id(user_id: int) -> int | None:
    from services.db import db
    with db() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM mood_sessions
            WHERE user_id=? AND pre_score IS NULL AND COALESCE(audio_sent,0)=0
              AND COALESCE(source,'') IN ('auto','settings')
              AND COALESCE(kind,'') IN ('work','home')
            ORDER BY id DESC
            LIMIT 1
            """.strip(),
            (int(user_id),),
        ).fetchone()
    return int(row['id']) if row else None




def find_pending_post_session_id(user_id: int) -> int | None:
    from services.db import db
    with db() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM mood_sessions
            WHERE user_id=? AND pre_score IS NOT NULL AND post_score IS NULL AND COALESCE(audio_sent,0)=1
              AND COALESCE(source,'') IN ('auto','settings')
              AND COALESCE(kind,'') IN ('work','home')
            ORDER BY id DESC
            LIMIT 1
            """.strip(),
            (int(user_id),),
        ).fetchone()
    return int(row['id']) if row else None

@dataclass(frozen=True)
class MoodTextFlowResult:
    ok: bool
    message: str
    prompt_done: bool = False
    delivered_platform: str | None = None
    transport: str | None = None


async def complete_pre_score_and_send(
    user_id: int,
    *,
    platform: str,
    score: int,
    senders: SenderRegistry,
    telegram_bot: Any | None = None,
) -> MoodTextFlowResult:
    session_id = find_pending_pre_session_id(int(user_id))
    if session_id is None:
        return MoodTextFlowResult(False, 'Сейчас нет активного ожидания оценки перед автотрансом.')
    session = get_session(session_id)
    if session is None:
        return MoodTextFlowResult(False, 'Не нашёл активную сессию авто-оценки.')
    if not set_pre(session_id, int(score)):
        return MoodTextFlowResult(False, 'Не удалось сохранить оценку. Попробуйте ещё раз.')

    log_audio_timeline_event(
        int(user_id),
        event_type='pre_score_received',
        sequence_key='full_series',
        anchor=int(session.anchor_id) if session.anchor_id is not None else None,
        title=None,
        platform=platform,
        meta_json=json.dumps({'score': int(score), 'kind': session.kind, 'source': session.source}, ensure_ascii=False),
        slot=str(session.slot) if session.slot else ('morning' if session.kind == 'work' else 'evening'),
    )

    anchor = int(session.anchor_id) if session.anchor_id is not None else None
    anchored = get_by_anchor(anchor) if anchor is not None else None
    if anchored is None or not anchored.path.exists():
        return MoodTextFlowResult(False, 'Не удалось найти аудиофайл для этого касания.')

    item = AudioProgressItem(ordinal=0, anchor=int(anchored.anchor), title=str(anchored.clean_title), path=anchored.path)
    plan = build_delivery_plan(int(user_id), preferred_platform=platform, fallback=platform)
    if not plan.external_user_id:
        return MoodTextFlowResult(False, 'Не найден идентификатор пользователя для выбранного мессенджера.')

    delivered_platform = plan.platform
    transport = None
    if plan.platform == MessengerPlatform.TELEGRAM.value:
        if telegram_bot is None:
            raise UnsupportedMessengerDelivery('Telegram bot instance is required for telegram auto mood flow')
        from services.fast_send_audio import send_audio_cached
        await send_audio_cached(
            telegram_bot,
            int(plan.external_user_id),
            key=f'auto_audio:{item.path.name}',
            file_path=item.path,
            caption=f'🎧 Ваш аудиотранс: №{item.anchor} — {item.title}',
            protect_content=True,
        )
        mark_pending_audio_delivery(int(user_id), item=item, platform=plan.platform, token=None)
        log_audio_timeline_event(int(user_id), event_type='telegram_sent', sequence_key='full_series', anchor=int(item.anchor), title=item.title, platform=plan.platform, slot=str(session.slot) if session.slot else ('morning' if session.kind == 'work' else 'evening'))
        transport = 'telegram_audio_pending'
    elif plan.platform == MessengerPlatform.MAX.value:
        sender = senders.get(MessengerPlatform.MAX.value)
        if sender is None:
            raise UnsupportedMessengerDelivery('No MAX sender registered')
        try:
            opus_path = ensure_max_opus_file(item.path)
            await sender.send_audio_file(plan.external_user_id, opus_path, caption=f'🎧 Ваш аудиотранс: №{item.anchor} — {item.title}')
            mark_pending_audio_delivery(int(user_id), item=item, platform=plan.platform, token=None)
            log_audio_timeline_event(int(user_id), event_type='native_audio_sent', sequence_key='full_series', anchor=int(item.anchor), title=item.title, platform=plan.platform, slot=str(session.slot) if session.slot else ('morning' if session.kind == 'work' else 'evening'))
            transport = 'max_native_audio_pending'
        except (RuntimeError, ValueError, TypeError, OSError, UnsupportedMessengerDelivery) as exc:
            log_audio_timeline_event(
                int(user_id),
                event_type='native_audio_fallback',
                sequence_key='full_series',
                anchor=int(item.anchor),
                title=item.title,
                platform=plan.platform,
                slot=str(session.slot) if session.slot else ('morning' if session.kind == 'work' else 'evening'),
            )
            raise UnsupportedMessengerDelivery(
                'MAX native .opus audio delivery failed in pre-score flow; refusing link fallback.'
            ) from exc
    else:
        sender = senders.get(MessengerPlatform.VK.value)
        if sender is None:
            raise UnsupportedMessengerDelivery('No VK sender registered')
        try:
            from services.messenger.audio_delivery import _post_audio_control_kwargs, _post_audio_controls_text
            await sender.send_audio_file(
                plan.external_user_id,
                item.path,
                caption=f'🎧 Ваш аудиотранс: №{item.anchor} — {item.title}',
                **_post_audio_control_kwargs(MessengerPlatform.VK.value),
            )
            await sender.send_text(
                plan.external_user_id,
                _post_audio_controls_text(MessengerPlatform.VK.value, item),
                **_post_audio_control_kwargs(MessengerPlatform.VK.value),
            )
            mark_pending_audio_delivery(int(user_id), item=item, platform=plan.platform, token=None)
            log_audio_timeline_event(
                int(user_id),
                event_type='native_audio_sent',
                sequence_key='full_series',
                anchor=int(item.anchor),
                title=item.title,
                platform=plan.platform,
                slot=str(session.slot) if session.slot else ('morning' if session.kind == 'work' else 'evening'),
            )
            transport = 'vk_native_audio_pending'
        except (RuntimeError, ValueError, TypeError, OSError, UnsupportedMessengerDelivery):
            access_token = issue_or_reuse_audio_access_token(int(user_id), item=item, platform=plan.platform)
            public_url = build_audio_access_url(access_token)
            if not public_url:
                raise UnsupportedMessengerDelivery('MESSENGER_PUBLIC_BASE_URL is empty; cannot deliver auto audio link for VK')
            await sender.send_text(
                plan.external_user_id,
                f'🎧 Ваш аудиотранс готов: №{item.anchor} — {item.title}\n\n'
                f'Слушать: {public_url}\n\n'
                'Это аварийная ссылка на файл: native-отправка ВКонтакте сейчас не прошла.',
            )
            log_audio_timeline_event(
                int(user_id),
                event_type='link_sent',
                sequence_key='full_series',
                anchor=int(item.anchor),
                title=item.title,
                platform=plan.platform,
                token=access_token,
                slot=str(session.slot) if session.slot else ('morning' if session.kind == 'work' else 'evening'),
            )
            transport = 'vk_link'

    register_touch(int(user_id), 'morning' if session.kind == 'work' else 'evening')
    advance(int(user_id), 'morning' if session.kind == 'work' else 'evening')
    mark_audio_sent(session_id)
    record_audio_delivery(int(user_id), item=item, platform=plan.platform)
    if transport in {'telegram_audio_pending', 'max_native_audio_pending', 'vk_native_audio_pending'}:
        message = (
            f'✅ Оценку {score:+d} сохранил. Отправил аудио №{item.anchor} — {item.title}.\n\n'
            'Когда дослушаете, напишите: done / готово / прослушал — и я сразу пришлю следующее.'
        )
        prompt_done = True
    else:
        message = f'✅ Оценку {score:+d} сохранил. Отправил ваш аудиотранс: №{item.anchor} — {item.title}.'
        prompt_done = False
    log_event(int(user_id), 'mood_score', {'stage': 'pre', 'value': int(score), 'kind': session.kind, 'source': session.source, 'platform': delivered_platform})
    return MoodTextFlowResult(True, message, prompt_done=prompt_done, delivered_platform=delivered_platform, transport=transport)


async def complete_post_score_and_send_next(
    user_id: int,
    *,
    platform: str,
    score: int,
    senders: SenderRegistry,
    telegram_bot: Any | None = None,
) -> MoodTextFlowResult:
    session_id = find_pending_post_session_id(int(user_id))
    if session_id is None:
        return MoodTextFlowResult(False, 'Сейчас нет активного ожидания оценки после прослушивания.')
    session = get_session(session_id)
    if session is None:
        return MoodTextFlowResult(False, 'Не нашёл сессию для оценки после прослушивания.')
    if not set_post(session_id, int(score)):
        return MoodTextFlowResult(False, 'Не удалось сохранить оценку после прослушивания. Попробуйте ещё раз.')

    comp = last_delta(int(user_id), kind=session.kind or '')
    delta = None
    if session.pre_score is not None:
        delta = int(score) - int(session.pre_score)
    delta_text = f' Изменение: {delta:+d}.' if delta is not None else ''
    avg = comp.get('avg_delta')
    avg_text = f' Средняя динамика по последним дням: {int(avg):+d}.' if avg is not None else ''

    message = (
        f'✅ Оценку после прослушивания {int(score):+d} сохранил.{delta_text}{avg_text}\n\n'
        'Цикл этого аудио завершён.\n\n'
        'Чтобы продолжить маршрут, нажмите «🎧 Получить аудио» или отправьте continue. '
        'Следующее аудио начнётся правильно: сначала шкала состояния ДО прослушивания, потом аудио, потом шкала ПОСЛЕ.'
    )
    transport = 'post_score_saved'
    delivered_platform = platform
    log_audio_timeline_event(
        int(user_id),
        event_type='post_score_received',
        sequence_key='full_series',
        anchor=int(session.anchor_id) if session.anchor_id is not None else None,
        title=None,
        platform=platform,
        meta_json=json.dumps({'score': int(score), 'kind': session.kind, 'source': session.source, 'delta': int(delta) if delta is not None else None}, ensure_ascii=False),
        slot=str(session.slot) if session.slot else ('morning' if session.kind == 'work' else 'evening'),
    )
    log_event(int(user_id), 'mood_score', {'stage': 'post', 'value': int(score), 'kind': session.kind, 'source': session.source, 'platform': delivered_platform})
    return MoodTextFlowResult(True, message, prompt_done=False, delivered_platform=delivered_platform, transport=transport)
