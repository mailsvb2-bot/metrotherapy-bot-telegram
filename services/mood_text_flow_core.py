from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from runtime.messenger_transport_errors import MessengerTransportError
from services.audio_anchor import get_by_anchor
from services.audio_guard import pick_demo_file
from core.time_utils import utc_now
from services.db import db, tx
from services.events import log_event
from services.messenger.audio_progress import AudioProgressItem, mark_pending_audio_delivery
from services.messenger.max_audio import ensure_max_opus_file, ensure_vk_opus_file
from services.messenger.outbound import (
    SenderRegistry,
    UnsupportedMessengerDelivery,
    build_delivery_plan,
)
from services.messenger.platforms import MessengerPlatform
from services.messenger.timeline import log_audio_timeline_event
from services.mood import get_session, last_delta, mark_audio_sent, set_post, set_pre
from services.practice_tokens import check_and_reserve_for_audio, finalize_audio_access
from services.progress import advance
from services.subscription import register_touch

NATIVE_AUDIO_REQUIRED_MESSAGE = (
    "⚠️ Не удалось отправить аудио прямо в этот мессенджер. "
    "Для VK используется безопасная ссылка, если провайдер отклоняет аудио-вложение. "
    "Для остальных каналов попробуйте ещё раз позже или сообщите администратору."
)


def parse_score_text(text: str | None) -> int | None:
    raw = (text or "").strip().replace("−", "-")
    if not raw:
        return None
    if raw.startswith("/score "):
        raw = raw.split(maxsplit=1)[1].strip()
    if raw.startswith("score "):
        raw = raw.split(maxsplit=1)[1].strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if -10 <= value <= 10:
        return value
    return None


def _native_audio_failure_meta(exc: BaseException) -> str:
    return json.dumps(
        {"error_type": type(exc).__name__, "error": str(exc)[:700]},
        ensure_ascii=False,
    )


def find_pending_pre_session_id(user_id: int) -> int | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM mood_sessions
            WHERE user_id=? AND COALESCE(audio_sent,0)=0
              AND COALESCE(source,'') IN ('auto','settings','demo')
              AND COALESCE(kind,'') IN ('work','home')
            ORDER BY id DESC
            LIMIT 1
            """.strip(),
            (int(user_id),),
        ).fetchone()
    return int(row["id"]) if row else None


def find_pending_post_session_id(user_id: int) -> int | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM mood_sessions
            WHERE user_id=? AND pre_score IS NOT NULL AND post_score IS NULL AND COALESCE(audio_sent,0)=1
              AND COALESCE(source,'') IN ('auto','settings','demo')
              AND COALESCE(kind,'') IN ('work','home')
            ORDER BY id DESC
            LIMIT 1
            """.strip(),
            (int(user_id),),
        ).fetchone()
    return int(row["id"]) if row else None


def _after_audio_controls_text(platform: str, item: AudioProgressItem) -> str:
    platform_title = (
        "MAX"
        if platform == MessengerPlatform.MAX.value
        else "ВКонтакте"
        if platform == MessengerPlatform.VK.value
        else platform
    )
    return (
        f"✅ Аудио №{item.anchor} — {item.title} отправлено прямо в {platform_title}.\n\n"
        "Когда прослушаете — нажмите кнопку «✅ Прослушал» ниже "
        "или отправьте done / готово / прослушал.\n\n"
        "После этого я покажу шкалу состояния ПОСЛЕ от −10 до +10."
    )


def _demo_item_for_kind(kind: str) -> AudioProgressItem | None:
    normalized = "work" if str(kind or "").strip() == "work" else "home"
    path = pick_demo_file(normalized)
    if not path or not path.exists():
        return None
    title = (
        "ресурсный демо-транс: утро / дорога"
        if normalized == "work"
        else "ресурсный демо-транс: вечер / домой"
    )
    anchor = 1 if normalized == "work" else 2
    return AudioProgressItem(ordinal=0, anchor=anchor, title=title, path=path)


def _record_demo_delivery_once(user_id: int, *, kind: str, session_id: int) -> None:
    """Persist cross-messenger demo usage exactly once per mood session."""

    message_id = -abs(int(session_id))
    sent_at = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO demo_events(
                    user_id, kind, message_id, sent_at_utc, voice_duration_sec
                ) VALUES(?,?,?,?,?)
                """.strip(),
                (int(user_id), str(kind), message_id, sent_at, None),
            )
            inserted = int(getattr(cursor, "rowcount", 0) or 0) == 1
    if inserted:
        log_event(
            int(user_id),
            "demo_sent",
            {"kind": str(kind), "message_id": message_id, "duration": None},
        )


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
    session_id: int | None = None,
) -> MoodTextFlowResult:
    """Complete pre-score and deliver exactly one audio under the token contract."""

    resolved_session_id = (
        int(session_id) if session_id is not None else find_pending_pre_session_id(int(user_id))
    )
    if resolved_session_id is None:
        return MoodTextFlowResult(False, "Сейчас нет активного ожидания оценки перед аудио.")
    session = get_session(resolved_session_id)
    if session is None:
        return MoodTextFlowResult(False, "Не нашёл активную сессию оценки.")
    if int(session.user_id) != int(user_id):
        return MoodTextFlowResult(False, "Эта сессия принадлежит другому пользователю.")
    if int(getattr(session, "audio_sent", 0) or 0) == 1:
        return MoodTextFlowResult(
            True,
            "🎧 Аудио по этой оценке уже было выдано. После прослушивания нажмите «Прослушал».",
            prompt_done=True,
            delivered_platform=platform,
            transport="already_sent",
        )

    session_id = int(resolved_session_id)
    source = str(session.source or "")
    is_demo = source == "demo"
    sequence_key = "demo" if is_demo else "full_series"

    if is_demo:
        item = _demo_item_for_kind(str(session.kind or "work"))
        if item is None:
            return MoodTextFlowResult(False, "Не удалось найти демо-аудиофайл для этого маршрута.")
    else:
        anchor = int(session.anchor_id) if session.anchor_id is not None else None
        anchored = get_by_anchor(anchor) if anchor is not None else None
        if anchored is None or not anchored.path.exists():
            return MoodTextFlowResult(False, "Не удалось найти аудиофайл для этого касания.")
        item = AudioProgressItem(
            ordinal=0,
            anchor=int(anchored.anchor),
            title=str(anchored.clean_title),
            path=anchored.path,
        )

    access_decision = check_and_reserve_for_audio(
        int(user_id),
        is_demo=is_demo,
        session_id=int(session_id),
        audio_anchor=int(item.anchor),
    )
    if not access_decision.allowed:
        return MoodTextFlowResult(
            False,
            access_decision.message or "🔐 Для продолжения нужна доступная практика.",
        )

    if not set_pre(session_id, int(score)):
        finalize_audio_access(access_decision, delivered=False)
        return MoodTextFlowResult(False, "Не удалось сохранить оценку. Попробуйте ещё раз.")

    log_audio_timeline_event(
        int(user_id),
        event_type="pre_score_received",
        sequence_key=sequence_key,
        anchor=int(item.anchor),
        title=item.title,
        platform=platform,
        meta_json=json.dumps(
            {"score": int(score), "kind": session.kind, "source": session.source},
            ensure_ascii=False,
        ),
        slot=str(session.slot)
        if session.slot
        else ("demo" if is_demo else ("morning" if session.kind == "work" else "evening")),
    )

    plan = build_delivery_plan(int(user_id), preferred_platform=platform, fallback=platform)
    if not plan.external_user_id:
        finalize_audio_access(access_decision, delivered=False)
        return MoodTextFlowResult(
            False,
            "Не найден идентификатор пользователя для выбранного мессенджера.",
        )

    delivered_platform = plan.platform
    transport: str | None = None

    try:
        if plan.platform == MessengerPlatform.TELEGRAM.value:
            if telegram_bot is None:
                raise UnsupportedMessengerDelivery(
                    "Telegram bot instance is required for telegram mood flow"
                )
            from services.fast_send_audio import send_audio_cached

            await send_audio_cached(
                telegram_bot,
                int(plan.external_user_id),
                key=f'{"demo" if is_demo else "auto"}_audio:{item.path.name}',
                file_path=item.path,
                caption=(
                    f"🎧 Ваш аудиотранс: №{item.anchor} — {item.title}"
                    if not is_demo
                    else f"✨ Ваш {item.title} готов."
                ),
                protect_content=True,
            )
            mark_pending_audio_delivery(
                int(user_id), item=item, platform=plan.platform, token=None, sequence_key=sequence_key
            )
            log_audio_timeline_event(
                int(user_id),
                event_type="telegram_sent",
                sequence_key=sequence_key,
                anchor=int(item.anchor),
                title=item.title,
                platform=plan.platform,
                slot=str(session.slot)
                if session.slot
                else ("demo" if is_demo else ("morning" if session.kind == "work" else "evening")),
            )
            transport = "telegram_audio_pending"

        elif plan.platform == MessengerPlatform.MAX.value:
            sender = senders.get(MessengerPlatform.MAX.value)
            if sender is None:
                raise UnsupportedMessengerDelivery("No MAX sender registered")
            opus_path = await asyncio.to_thread(ensure_max_opus_file, item.path)
            await sender.send_audio_file(
                plan.external_user_id,
                opus_path,
                caption=f"🎧 Ваш аудиотранс: №{item.anchor} — {item.title}",
            )
            mark_pending_audio_delivery(
                int(user_id), item=item, platform=plan.platform, token=None, sequence_key=sequence_key
            )
            log_audio_timeline_event(
                int(user_id),
                event_type="native_audio_sent",
                sequence_key=sequence_key,
                anchor=int(item.anchor),
                title=item.title,
                platform=plan.platform,
                slot=str(session.slot)
                if session.slot
                else ("demo" if is_demo else ("morning" if session.kind == "work" else "evening")),
            )
            transport = "max_native_audio_pending"

        else:
            sender = senders.get(MessengerPlatform.VK.value)
            if sender is None:
                raise UnsupportedMessengerDelivery("No VK sender registered")
            try:
                vk_audio_path = await asyncio.to_thread(ensure_vk_opus_file, item.path)
                await sender.send_audio_file(
                    plan.external_user_id,
                    vk_audio_path,
                    caption=f"🎧 Ваш аудиотранс: №{item.anchor} — {item.title}",
                )
            except (
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
                    sequence_key=sequence_key,
                    anchor=int(item.anchor),
                    title=item.title,
                    platform=plan.platform,
                    meta_json=_native_audio_failure_meta(exc),
                    slot=str(session.slot)
                    if session.slot
                    else ("demo" if is_demo else ("morning" if session.kind == "work" else "evening")),
                )
                from services.messenger.audio_delivery import send_vk_audio_access_link

                result = await send_vk_audio_access_link(
                    user_id=int(user_id),
                    external_user_id=plan.external_user_id,
                    sender=sender,
                    item=item,
                    replay=False,
                    sequence_key=sequence_key,
                )
                transport = result.transport
            else:
                mark_pending_audio_delivery(
                    int(user_id), item=item, platform=plan.platform, token=None, sequence_key=sequence_key
                )
                log_audio_timeline_event(
                    int(user_id),
                    event_type="native_audio_sent",
                    sequence_key=sequence_key,
                    anchor=int(item.anchor),
                    title=item.title,
                    platform=plan.platform,
                    slot=str(session.slot)
                    if session.slot
                    else ("demo" if is_demo else ("morning" if session.kind == "work" else "evening")),
                )
                transport = "vk_native_audio_pending"
    except (
        RuntimeError,
        ValueError,
        TypeError,
        OSError,
        UnsupportedMessengerDelivery,
        MessengerTransportError,
    ) as exc:  # validator: allow-wide-except
        finalize_audio_access(access_decision, delivered=False)
        log_audio_timeline_event(
            int(user_id),
            event_type="practice_audio_send_failed",
            sequence_key=sequence_key,
            anchor=int(item.anchor),
            title=item.title,
            platform=plan.platform,
            meta_json=_native_audio_failure_meta(exc),
        )
        raise

    if is_demo:
        _record_demo_delivery_once(
            int(user_id),
            kind="work" if str(session.kind or "") == "work" else "home",
            session_id=int(session_id),
        )
    mark_audio_sent(session_id)
    finalized = finalize_audio_access(access_decision, delivered=True)
    if not finalized:
        log_event(
            int(user_id),
            "practice_token_finalize_deferred",
            {
                "session_id": int(session_id),
                "audio_anchor": int(item.anchor),
                "platform": delivered_platform,
            },
        )

    if not is_demo:
        register_touch(int(user_id), "morning" if session.kind == "work" else "evening")
        advance(int(user_id), "morning" if session.kind == "work" else "evening")

    if transport == "telegram_audio_pending":
        message = (
            f"✅ Оценку {score:+d} сохранил. Отправил аудио №{item.anchor} — {item.title}.\n\n"
            "Когда дослушаете, напишите: done / готово / прослушал — и я покажу шкалу ПОСЛЕ."
        )
        prompt_done = True
    elif transport in {
        "max_native_audio_pending",
        "vk_native_audio_pending",
        "vk_audio_access_link_pending",
    }:
        message = _after_audio_controls_text(plan.platform, item)
        prompt_done = True
    else:
        message = ""
        prompt_done = False

    if access_decision.warning:
        message = f"{access_decision.warning}\n\n{message}".strip()

    log_event(
        int(user_id),
        "mood_score",
        {
            "stage": "pre",
            "value": int(score),
            "kind": session.kind,
            "source": session.source,
            "platform": delivered_platform,
        },
    )
    return MoodTextFlowResult(
        True,
        message,
        prompt_done=prompt_done,
        delivered_platform=delivered_platform,
        transport=transport,
    )


async def complete_post_score_and_send_next(
    user_id: int,
    *,
    platform: str,
    score: int,
    senders: SenderRegistry,
    telegram_bot: Any | None = None,
    session_id: int | None = None,
) -> MoodTextFlowResult:
    del senders, telegram_bot

    resolved_session_id = (
        int(session_id) if session_id is not None else find_pending_post_session_id(int(user_id))
    )
    if resolved_session_id is None:
        return MoodTextFlowResult(False, "Сейчас нет активного ожидания оценки после прослушивания.")
    session_id = int(resolved_session_id)
    session = get_session(session_id)
    if session is None:
        return MoodTextFlowResult(False, "Не нашёл сессию для оценки после прослушивания.")
    if int(session.user_id) != int(user_id):
        return MoodTextFlowResult(False, "Эта сессия принадлежит другому пользователю.")
    if session.post_score is not None:
        return MoodTextFlowResult(
            True,
            "✅ Оценка ПОСЛЕ по этой сессии уже сохранена.",
            delivered_platform=platform,
            transport="post_score_already_saved",
        )
    if not set_post(session_id, int(score)):
        return MoodTextFlowResult(
            False,
            "Не удалось сохранить оценку после прослушивания. Попробуйте ещё раз.",
        )

    comp = last_delta(int(user_id), kind=session.kind or "")
    delta = int(score) - int(session.pre_score) if session.pre_score is not None else None
    delta_text = f" Изменение: {delta:+d}." if delta is not None else ""
    avg = comp.get("avg_delta")
    avg_text = f" Средняя динамика по последним дням: {int(avg):+d}." if avg is not None else ""
    is_demo = str(session.source or "") == "demo"

    if is_demo:
        message = (
            f"✅ Оценку после демо {int(score):+d} сохранил.{delta_text}{avg_text}\n\n"
            "Демо-цикл завершён: шкала ДО → аудио → шкала ПОСЛЕ.\n\n"
            "Сейчас можно посмотреть график прогресса или продолжить маршрут через главное меню."
        )
        sequence_key = "demo"
    else:
        message = (
            f"✅ Оценку после прослушивания {int(score):+d} сохранил.{delta_text}{avg_text}\n\n"
            "Цикл этого аудио завершён.\n\n"
            "Чтобы продолжить маршрут, нажмите «🎧 Получить аудио» или отправьте continue. "
            "Следующее аудио снова начнётся со шкалы ДО."
        )
        sequence_key = "full_series"

    log_audio_timeline_event(
        int(user_id),
        event_type="post_score_received",
        sequence_key=sequence_key,
        anchor=int(session.anchor_id) if session.anchor_id is not None else None,
        title=None,
        platform=platform,
        meta_json=json.dumps(
            {
                "score": int(score),
                "kind": session.kind,
                "source": session.source,
                "delta": int(delta) if delta is not None else None,
            },
            ensure_ascii=False,
        ),
        slot=str(session.slot)
        if session.slot
        else ("demo" if is_demo else ("morning" if session.kind == "work" else "evening")),
    )
    log_event(
        int(user_id),
        "mood_score",
        {
            "stage": "post",
            "value": int(score),
            "kind": session.kind,
            "source": session.source,
            "platform": platform,
        },
    )
    return MoodTextFlowResult(
        True,
        message,
        prompt_done=False,
        delivered_platform=platform,
        transport="post_score_saved",
    )
