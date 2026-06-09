from __future__ import annotations
import logging
import sqlite3

from services.sla import record as sla_record
from services.bg import tm
from services.fast_send_audio import send_audio_cached

from datetime import timedelta
from core.time_utils import utc_now
from services.jobs import add_job, cancel_post_prompt

import asyncio

from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.types import CallbackQuery, BufferedInputFile
from aiogram.types import FSInputFile
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from keyboards.inline import kb_mood_scale, kb_mood_done, kb_body_question, kb_after_post_actions, kb_post_show_chart
from services.db import mark_delivery_once, unmark_delivery, was_delivered
from services.idempotency import wall_key
from services.idempotency_keys import for_demo_click, for_session
from services.mood import set_pre, set_post, get_session, mark_audio_sent, last_delta
from services.events import log_event
from services.audio_anchor import get_by_anchor
from services.catalog import AudioCatalog
# Контракт: запись факта отправки демо живёт в demo_analytics.
# В старых ветках файл мог называться demo_events — оставляем только корректный импорт.
from services.demo_analytics import record_demo_sent, demo_sent_kinds
from services.demo_policy import next_remaining_demo_kind
from services.body import pick_body_question, save_body_feedback, technique_for_area
from services.audio_cache import get_cached_file_id, save_cached_file_id
from services.messenger.audio_progress import record_audio_delivery, AudioProgressItem
from services.progress import advance
from services.support_ai import decide_support_pre
from services.subscription import register_touch


from core.callback_utils import safe_answer_callback
router = Router()


def _fmt_score(v):
    if v is None:
        return "—"
    return f"{int(v):+d}" if int(v) != 0 else "0"


def _trial_outcome_keyboard(user_id: int, kind: str, *, delta: int | None, session_id: int | None = None) -> InlineKeyboardMarkup:
    """Outcome-aware actions after a free demo post-score.

    The button set must not promise a free practice when TrialPolicy says both
    free demo kinds were already consumed.  This keeps UX aligned with the
    canonical demo access policy and avoids a hidden second trial brain.
    """

    rows: list[list[InlineKeyboardButton]] = []
    chart_callback = f"post:chart:{int(session_id)}" if session_id is not None else "settings:state"
    rows.append([InlineKeyboardButton(text="📈 Посмотреть график изменения", callback_data=chart_callback)])

    try:
        sent = demo_sent_kinds(int(user_id))
        remaining = next_remaining_demo_kind(kind, sent)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("trial keyboard: failed to read demo history")
        remaining = None
    except (TypeError, ValueError):
        logging.getLogger(__name__).exception("trial keyboard: bad demo history")
        remaining = None

    if delta is not None and delta < 0:
        # Safety-first: no direct payment CTA after negative self-reported outcome.
        if remaining:
            rows.append([InlineKeyboardButton(text="🌿 Попробовать позже другой маршрут", callback_data="demo")])
        rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    rows.append([InlineKeyboardButton(text="🔐 Открыть полный маршрут", callback_data="sub:menu")])
    if remaining:
        label = "🌙 Попробовать вечернюю практику" if remaining == "home" else "🚗 Попробовать утреннюю практику"
        rows.append([InlineKeyboardButton(text=label, callback_data="demo")])
    else:
        rows.append([InlineKeyboardButton(text="✅ Бесплатные практики завершены", callback_data="sub:menu")])
    rows.append([InlineKeyboardButton(text="🎁 Подарить подписку", callback_data="gift:menu")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _trial_outcome_text(*, pre: int | None, post: int | None, delta: int | None, avg_delta: int | None) -> str:
    base = (
        "✅ Зафиксировал состояние после демо-практики.\n\n"
        f"Сегодня: {_fmt_score(pre)} → {_fmt_score(post)} (изменение {_fmt_score(delta)})"
    )
    if avg_delta is not None:
        base += f"\nСредняя динамика за последние дни: {_fmt_score(avg_delta)}"

    if delta is None:
        return base + (
            "\n\nЯ сохранил результат. Полный маршрут нужен не для разового прослушивания, "
            "а чтобы встроить короткие практики в ритм дня."
        )

    if delta > 0:
        return base + (
            "\n\nЭто хороший сигнал: формат Вам подходит. "
            "Одна практика может дать сдвиг, но главный эффект Метротерапии — в регулярном ритме: "
            "утро, вечер или оба маршрута.\n\n"
            "Можно открыть полный маршрут и продолжить уже не вслепую, а отталкиваясь от Вашего первого результата."
        )

    if delta == 0:
        return base + (
            "\n\nЯвного сдвига пока нет — это нормально. "
            "Иногда человеку лучше подходит другой момент дня: утро/дорога или вечер/домой.\n\n"
            "Можно попробовать второй бесплатный маршрут или посмотреть, что входит в полный маршрут."
        )

    return base + (
        "\n\nЯ вижу, что по Вашей оценке после практики стало тяжелее. "
        "Сейчас лучше не усиливать нагрузку и не торопиться с продолжением.\n\n"
        "Сделайте паузу. Если состояние острое или небезопасное — обратитесь за живой профессиональной помощью. "
        "К практике можно вернуться позже, в более мягком темпе."
    )




@router.callback_query(F.data.regexp(r"^mood:(pre|post):\d+:-?\d+$"))
async def mood_answer(cb: CallbackQuery):
    """Клик по шкале самооценки.

    callback_data:
      mood:<stage>:<session_id>:<value>

    UX:
    - после клика "до" аудио отправляется СРАЗУ (без подтверждений и пауз);
    - после кнопки "Прослушал" показываем шкалу "после";
    - после "после" — быстрый анализ + outcome-aware try-before-buy предложение.
    """
    # Сразу отвечаем на callback, чтобы у пользователя не висел "часик".
    # Дальше тяжёлые операции (отправка аудио) можно выполнить без блокировки UI.
    await safe_answer_callback(cb)

    data = (cb.data or "").split(":")
    if len(data) != 4:
        return
    _, stage, sid_raw, val_raw = data
    try:
        sid = int(sid_raw)
        val = int(val_raw)
    except (ValueError, RuntimeError):
        logging.getLogger(__name__).exception("Unhandled exception")
        return

    s = await asyncio.to_thread(get_session, sid)
    if not s:
        return

    ok = False
    if stage == "pre":
        ok = await asyncio.to_thread(set_pre, sid, val)
    elif stage == "post":
        ok = await asyncio.to_thread(set_post, sid, val)

    if not ok:
        return await cb.message.answer("⚠️ Не удалось сохранить оценку. Попробуйте ещё раз.")
    # Мгновенное подтверждение делаем через cb.answer() в начале (без лишнего сообщения в чат)

    log_event(int(cb.from_user.id), "mood_score", {"stage": stage, "value": val, "kind": s.kind, "source": s.source})

    # --- STAGE: PRE -> send audio immediately (once) ---
    if stage == "pre":
        # Если аудио уже отправляли по этой сессии — ничего не дублируем.
        try:
            if getattr(s, "audio_sent", 0):
                return
        except (ValueError, RuntimeError):
            logging.getLogger(__name__).exception("Unhandled exception")

        # --- Support-AI: персональная реакция системы (деликатно, без давления) ---
        try:
            dec = decide_support_pre(
                user_id=int(cb.from_user.id),
                kind=str(s.kind or "both"),
                # Демо можно сопровождать и без подписки, но НЕ обещаем персонализацию подписки.
                require_subscription=(str(s.source) != "demo"),
            )
            if dec and dec.message:
                await cb.message.answer(dec.message)
        except (ValueError, RuntimeError):
            logging.getLogger(__name__).exception("Unhandled exception")

        # Определяем что отправлять
        caption = None
        file_path = None

        if s.source == "auto" and s.anchor_id is not None:
            aa = get_by_anchor(int(s.anchor_id))
            if aa:
                file_path = aa.path
                caption = aa.clean_title
        elif s.source == "demo":
            # ДЕМО: выбираем по имени файла, а не по индексу.
            # Причина: AudioCatalog().get_demo() сортирует по имени, и при файлах
            #   audio/demo/home.opus, audio/demo/work.opus
            # порядок всегда home → work.
            demo_files = AudioCatalog().get_demo()
            if demo_files:
                want = "work" if (s.kind or "") == "work" else "home"
                picked = None
                for p in demo_files:
                    try:
                        stem = (p.stem or "").lower()
                    except (ValueError, RuntimeError):
                        stem = ""
                    if want in stem:
                        picked = p
                        break
                # fallback: если не нашли по имени — берём первый доступный
                file_path = picked or demo_files[0]
                caption = (
                    "✨ Ваш ресурсный демо-транс готов.\n\n"
                    "Наденьте наушники и просто слушайте — ничего специально делать не нужно."
                )

        # Если файл не найден — мягкая ошибка
        if not file_path or not file_path.exists():
            await cb.message.answer("⚠️ Не удалось найти аудиофайл. Попробуйте позже или сообщите в поддержку.")
            return
        # --- Idempotency (канон) ---
        # demo/full use the same two-phase lock: lock before send, final marker after send.
        user_id = int(cb.from_user.id)
        idem_kind = "demo" if s.source == "demo" else str(s.kind or "")
        idem_scheduled_at = for_demo_click() if s.source == "demo" else for_session(sid)

        if was_delivered(user_id, idem_kind, "audio", idem_scheduled_at):
            return
        if not mark_delivery_once(user_id, idem_kind, "audio_lock", idem_scheduled_at):
            return

        async def _send_audio() -> None:
            cached_kind = "voice" if file_path.suffix.lower() in (".ogg", ".opus") else "audio"
            cached_id = get_cached_file_id(file_path, cached_kind)

            try:
                if cached_id and cached_kind == "voice":
                    msg = await cb.bot.send_voice(
                        chat_id=int(cb.from_user.id),
                        voice=cached_id,
                        caption=caption,
                        protect_content=True,
                    )
                elif cached_id and cached_kind == "audio":
                    msg = await cb.bot.send_audio(
                        chat_id=int(cb.from_user.id),
                        audio=cached_id,
                        caption=caption,
                        protect_content=True,
                    )
                elif file_path.suffix.lower() in (".ogg", ".opus"):
                    msg = await cb.bot.send_voice(
                        chat_id=int(cb.from_user.id),
                        voice=FSInputFile(file_path),
                        caption=caption,
                        protect_content=True,
                    )
                else:
                    msg = await send_audio_cached(
                        bot=cb.bot,
                        chat_id=int(cb.from_user.id),
                        key=str(file_path),
                        file_path=file_path,
                        caption=caption,
                        protect_content=True,
                    )
            except (
                TelegramAPIError,
                TelegramNetworkError,
                OSError,
                asyncio.TimeoutError,
                sqlite3.Error,
                ValueError,
                RuntimeError,
            ) as e:
                try:
                    unmark_delivery(int(cb.from_user.id), idem_kind, "audio_lock", idem_scheduled_at)
                except sqlite3.Error:
                    logging.getLogger(__name__).debug("audio_lock cleanup failed", exc_info=True)

                log_event(
                    int(cb.from_user.id),
                    "mood_audio_send_error",
                    {"err": str(e), "err_type": type(e).__name__, "source": s.source},
                )

                try:
                    await cb.message.answer("⚠️ Не удалось отправить аудио. Попробуйте ещё раз.")
                except (TelegramAPIError, TelegramNetworkError, RuntimeError):
                    logging.getLogger(__name__).exception("failed to notify user about audio send error")
                return

            try:
                if getattr(msg, "voice", None) and getattr(msg.voice, "file_id", None):
                    save_cached_file_id(file_path, "voice", str(msg.voice.file_id))
                if getattr(msg, "audio", None) and getattr(msg.audio, "file_id", None):
                    save_cached_file_id(file_path, "audio", str(msg.audio.file_id))
            except (sqlite3.Error, ValueError, RuntimeError):
                logging.getLogger(__name__).exception("audio cache update failed")

            try:
                if s.source == "demo" and msg:
                    dur = None
                    if getattr(msg, "voice", None):
                        dur = getattr(msg.voice, "duration", None)
                    if getattr(msg, "audio", None):
                        dur = getattr(msg.audio, "duration", dur)

                    from datetime import datetime, timezone
                    sent_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                    record_demo_sent(
                        int(cb.from_user.id),
                        "work" if s.kind == "work" else "home",
                        int(msg.message_id),
                        sent_at,
                        int(dur) if dur else None,
                    )
            except (sqlite3.Error, ValueError, RuntimeError):
                logging.getLogger(__name__).exception("demo analytics update failed")

            try:
                if s.source != "demo":
                    slot = "morning" if s.kind == "work" else "evening"
                    register_touch(int(cb.from_user.id), slot)
                    advance(int(cb.from_user.id), slot)
            except (sqlite3.Error, ValueError, RuntimeError):
                logging.getLogger(__name__).exception("subscription touch update failed")

            try:
                if s.anchor_id is not None and file_path is not None:
                    record_audio_delivery(
                        int(cb.from_user.id),
                        item=AudioProgressItem(
                            ordinal=0,
                            anchor=int(s.anchor_id),
                            title=str(caption or file_path.stem),
                            path=file_path,
                        ),
                        platform="telegram",
                    )
            except (sqlite3.Error, ValueError, RuntimeError):
                logging.getLogger(__name__).exception("audio progress update failed")

            final_marker_ok = False
            try:
                final_marker_ok = mark_delivery_once(
                    int(cb.from_user.id),
                    idem_kind,
                    "audio",
                    idem_scheduled_at,
                )
                if not final_marker_ok:
                    final_marker_ok = was_delivered(
                        int(cb.from_user.id),
                        idem_kind,
                        "audio",
                        idem_scheduled_at,
                    )
            except sqlite3.Error:
                logging.getLogger(__name__).exception("audio final idempotency marker failed")

            if final_marker_ok:
                try:
                    unmark_delivery(int(cb.from_user.id), idem_kind, "audio_lock", idem_scheduled_at)
                except sqlite3.Error:
                    logging.getLogger(__name__).debug("audio_lock cleanup after send failed", exc_info=True)
            else:
                log_event(
                    int(cb.from_user.id),
                    "mood_audio_final_marker_missing",
                    {"source": s.source, "kind": idem_kind, "scheduled_at": idem_scheduled_at},
                )

            try:
                mark_audio_sent(sid)
            except (sqlite3.Error, ValueError, RuntimeError):
                logging.getLogger(__name__).exception("mark_audio_sent failed")

            try:
                await cb.message.answer("Когда прослушаете — нажмите кнопку:", reply_markup=kb_mood_done(sid))
            except (TelegramAPIError, TelegramNetworkError, RuntimeError):
                logging.getLogger(__name__).exception("failed to send mood-done prompt")

        tm().create(_send_audio())
        return

    # --- STAGE: POST -> quick compare + outcome-aware try-before-buy next action ---
    if stage == "post":
        # Обновляем сессию после set_post(), иначе локальный объект s содержит старый post_score.
        s_after = await asyncio.to_thread(get_session, sid)
        pre_score = getattr(s_after, "pre_score", None) if s_after else None
        post_score = getattr(s_after, "post_score", None) if s_after else val
        delta = (int(post_score) - int(pre_score)) if pre_score is not None and post_score is not None else None

        comp = last_delta(int(cb.from_user.id), kind=(getattr(s_after, "kind", s.kind) or ""))
        ad = comp.get("avg_delta")
        kind = str(getattr(s_after, "kind", s.kind) or "")
        source = str(getattr(s_after, "source", s.source) or "")

        if source == "demo":
            if delta is not None:
                if delta > 0:
                    log_event(int(cb.from_user.id), "trial_delta_positive", {"kind": kind, "delta": delta, "session_id": sid})
                elif delta < 0:
                    log_event(int(cb.from_user.id), "trial_delta_negative", {"kind": kind, "delta": delta, "session_id": sid})
                else:
                    log_event(int(cb.from_user.id), "trial_delta_neutral", {"kind": kind, "delta": delta, "session_id": sid})
            log_event(int(cb.from_user.id), "trial_outcome_recorded", {"kind": kind, "delta": delta, "session_id": sid})
            await cb.message.answer(
                _trial_outcome_text(pre=pre_score, post=post_score, delta=delta, avg_delta=ad),
                reply_markup=_trial_outcome_keyboard(int(cb.from_user.id), kind, delta=delta, session_id=sid),
            )
            return

        msg = (
            "✅ Зафиксировал состояние после транса.\n\n"
            f"Сегодня: {_fmt_score(pre_score)} → {_fmt_score(post_score)} (изменение {_fmt_score(delta)})"
        )
        if ad is not None:
            msg += f"\nСредняя динамика за последние дни: {_fmt_score(ad)}"

        await cb.message.answer(msg, reply_markup=kb_post_show_chart(sid))
        return
