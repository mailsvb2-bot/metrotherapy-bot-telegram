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
from services.mood import get_session
from services.mood_text_flow import complete_post_score_and_send_next, complete_pre_score_and_send
from services.support_ai import decide_support_pre
from services.trial_conversion_flow import plan_trial_conversion_after_outcome

router = Router()
log = logging.getLogger(__name__)


def _callback_message(cb: CallbackQuery) -> Message | None:
    return cb.message if isinstance(cb.message, Message) else None


def _trial_outcome_keyboard(
    user_id: int,
    kind: str,
    *,
    allow_paid_cta: bool,
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

    if not allow_paid_cta:
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

    if str(session_after.source or "") == "demo":
        plan = await asyncio.to_thread(
            plan_trial_conversion_after_outcome,
            user_id,
            session_id,
            platform="telegram",
        )
        if plan is None:
            await message.answer(result.message, reply_markup=kb_post_show_chart(session_id))
            return
        await message.answer(
            plan.message,
            reply_markup=_trial_outcome_keyboard(
                user_id,
                plan.kind,
                allow_paid_cta=plan.allow_paid_cta,
                session_id=session_id,
            ),
        )
        return

    await message.answer(result.message, reply_markup=kb_post_show_chart(session_id))
