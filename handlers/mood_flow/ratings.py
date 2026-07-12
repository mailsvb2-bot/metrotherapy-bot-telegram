from __future__ import annotations

import asyncio
import logging
import sqlite3

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.callback_utils import safe_answer_callback
from keyboards.inline import kb_mood_done, kb_post_show_chart
from runtime.messenger_senders import TelegramBotSender
from services.demo_analytics import demo_sent_kinds
from services.demo_policy import next_remaining_demo_kind
from services.events import log_event
from services.messenger.outbound import SenderRegistry
from services.mood import get_session, last_delta
from services.mood_text_flow import complete_post_score_and_send_next, complete_pre_score_and_send
from services.support_ai import decide_support_pre

router = Router()
log = logging.getLogger(__name__)


def _callback_message(cb: CallbackQuery) -> Message | None:
    return cb.message if isinstance(cb.message, Message) else None


def _fmt_score(value: object) -> str:
    if value is None:
        return "—"
    parsed = int(value)
    return f"{parsed:+d}" if parsed != 0 else "0"


def _trial_outcome_keyboard(
    user_id: int,
    kind: str,
    *,
    delta: int | None,
    session_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    chart_callback = f"post:chart:{int(session_id)}" if session_id is not None else "settings:state"
    rows.append([
        InlineKeyboardButton(text="📈 Посмотреть график изменения", callback_data=chart_callback)
    ])

    try:
        sent = demo_sent_kinds(int(user_id))
        remaining = next_remaining_demo_kind(kind, sent)
    except sqlite3.Error:
        log.exception("trial keyboard: failed to read demo history")
        remaining = None
    except TypeError:
        log.exception("trial keyboard: bad demo history")
        remaining = None
    except ValueError:
        log.exception("trial keyboard: bad demo history")
        remaining = None

    if delta is not None and delta < 0:
        if remaining:
            rows.append([
                InlineKeyboardButton(text="🌿 Попробовать позже другой маршрут", callback_data="demo")
            ])
        rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    rows.append([
        InlineKeyboardButton(text="🔐 Открыть полный маршрут", callback_data="sub:menu")
    ])
    if remaining:
        label = "🌙 Попробовать вечернюю практику" if remaining == "home" else "🚗 Попробовать утреннюю практику"
        rows.append([InlineKeyboardButton(text=label, callback_data="demo")])
    else:
        rows.append([
            InlineKeyboardButton(text="✅ Бесплатные практики завершены", callback_data="sub:menu")
        ])
    rows.append([InlineKeyboardButton(text="🎁 Подарить подписку", callback_data="gift:menu")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _trial_outcome_text(
    *,
    pre: int | None,
    post: int | None,
    delta: int | None,
    avg_delta: int | None,
) -> str:
    base = (
        "✅ Зафиксировал состояние после демо-практики.\n\n"
        f"Сегодня: {_fmt_score(pre)} → {_fmt_score(post)} "
        f"(изменение {_fmt_score(delta)})"
    )
    if avg_delta is not None:
        base += f"\nСредняя динамика за последние дни: {_fmt_score(avg_delta)}"

    if delta is None:
        return base + (
            "\n\nЯ сохранил результат. Полный маршрут нужен не для разового "
            "прослушивания, а чтобы встроить короткие практики в ритм дня."
        )
    if delta > 0:
        return base + (
            "\n\nЭто хороший сигнал: формат Вам подходит. Одна практика может дать "
            "сдвиг, но главный эффект Метротерапии — в регулярном ритме: утро, "
            "вечер или оба маршрута.\n\nМожно открыть полный маршрут и продолжить "
            "уже не вслепую, а отталкиваясь от Вашего первого результата."
        )
    if delta == 0:
        return base + (
            "\n\nЯвного сдвига пока нет — это нормально. Иногда человеку лучше подходит "
            "другой момент дня: утро/дорога или вечер/домой.\n\nМожно попробовать "
            "второй бесплатный маршрут или посмотреть, что входит в полный маршрут."
        )
    return base + (
        "\n\nЯ вижу, что по Вашей оценке после практики стало тяжелее. Сейчас лучше "
        "не усиливать нагрузку и не торопиться с продолжением.\n\nСделайте паузу. "
        "Если состояние острое или небезопасное — обратитесь за живой профессиональной "
        "помощью. К практике можно вернуться позже, в более мягком темпе."
    )


async def _send_pre_failure(message: Message, exc: BaseException) -> None:
    log.error("canonical mood pre delivery failed: %s", type(exc).__name__, exc_info=True)
    await message.answer("⚠️ Не удалось отправить аудио. Практика не списана; попробуйте ещё раз.")


@router.callback_query(F.data.regexp(r"^mood:(pre|post):\d+:-?\d+$"))
async def mood_answer(cb: CallbackQuery) -> None:
    """Telegram adapter for the canonical PRE → audio → POST service flow."""

    await safe_answer_callback(cb)
    message = _callback_message(cb)
    if message is None:
        return

    parts = str(cb.data or "").split(":")
    if len(parts) != 4:
        return
    _, stage, sid_raw, value_raw = parts
    try:
        session_id = int(sid_raw)
        value = int(value_raw)
    except ValueError:
        return

    user_id = int(cb.from_user.id)
    session = await asyncio.to_thread(get_session, session_id)
    if session is None:
        await message.answer("ℹ️ Эта кнопка устарела. Откройте текущий маршрут заново.")
        return
    if int(session.user_id) != user_id:
        log_event(
            user_id,
            "foreign_mood_callback_rejected",
            {"session_id": session_id, "owner_user_id": int(session.user_id), "stage": stage},
        )
        await message.answer("ℹ️ Эта кнопка относится к другой сессии и не может быть использована.")
        return

    if stage == "pre":
        try:
            decision = decide_support_pre(
                user_id=user_id,
                kind=str(session.kind or "both"),
                require_subscription=(str(session.source or "") != "demo"),
            )
            if decision and decision.message:
                await message.answer(decision.message)
        except ValueError:
            log.debug("support pre decision skipped", exc_info=True)
        except RuntimeError:
            log.debug("support pre decision skipped", exc_info=True)

        bot = cb.bot
        if bot is None:
            await message.answer("⚠️ Не удалось открыть канал отправки аудио. Попробуйте ещё раз.")
            return
        registry = SenderRegistry(telegram=TelegramBotSender(bot))
        try:
            result = await complete_pre_score_and_send(
                user_id,
                platform="telegram",
                score=value,
                senders=registry,
                telegram_bot=bot,
                session_id=session_id,
            )
        except TelegramAPIError as exc:
            await _send_pre_failure(message, exc)
            return
        except OSError as exc:
            await _send_pre_failure(message, exc)
            return
        except RuntimeError as exc:
            await _send_pre_failure(message, exc)
            return
        except ValueError as exc:
            await _send_pre_failure(message, exc)
            return

        if not result.ok:
            await message.answer(result.message or "⚠️ Не удалось продолжить практику.")
            return
        await message.answer(
            result.message or "🎧 Аудио отправлено. Когда прослушаете — нажмите «Прослушал».",
            reply_markup=kb_mood_done(session_id),
        )
        return

    registry = SenderRegistry()
    result = await complete_post_score_and_send_next(
        user_id,
        platform="telegram",
        score=value,
        senders=registry,
        telegram_bot=cb.bot,
        session_id=session_id,
    )
    if not result.ok:
        await message.answer(result.message or "⚠️ Не удалось сохранить оценку.")
        return
    if result.transport == "post_score_already_saved":
        await message.answer(result.message)
        return

    session_after = await asyncio.to_thread(get_session, session_id)
    if session_after is None:
        await message.answer(result.message)
        return
    pre_score = session_after.pre_score
    post_score = session_after.post_score
    delta = (
        int(post_score) - int(pre_score)
        if pre_score is not None and post_score is not None
        else None
    )
    comparison = await asyncio.to_thread(last_delta, user_id, str(session_after.kind or ""))
    average_delta = comparison.get("avg_delta")

    if str(session_after.source or "") == "demo":
        if delta is not None:
            outcome = "positive" if delta > 0 else "negative" if delta < 0 else "neutral"
            log_event(
                user_id,
                f"trial_delta_{outcome}",
                {"kind": session_after.kind, "delta": delta, "session_id": session_id},
            )
        log_event(
            user_id,
            "trial_outcome_recorded",
            {"kind": session_after.kind, "delta": delta, "session_id": session_id},
        )
        await message.answer(
            _trial_outcome_text(
                pre=pre_score,
                post=post_score,
                delta=delta,
                avg_delta=int(average_delta) if average_delta is not None else None,
            ),
            reply_markup=_trial_outcome_keyboard(
                user_id,
                str(session_after.kind or ""),
                delta=delta,
                session_id=session_id,
            ),
        )
        return

    await message.answer(result.message, reply_markup=kb_post_show_chart(session_id))
