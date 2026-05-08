from __future__ import annotations
import logging
import sqlite3


from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router, F
from aiogram.types import CallbackQuery

from config.settings import settings
from keyboards.inline import kb_sales_offer
from services.demo_analytics import record_demo_ack, demo_sent_kinds
from services.demo_policy import can_repeat_demo_for_user
from services.jobs import add_job
from services.jobs import cancel_jobs
from services.store import store
from services.events import log_event
from services.db import db
from services.personalization import should_offer_micro_question, get_micro_question
from keyboards.inline import kb_micro_question

from core.callback_utils import safe_answer_callback
router = Router()
UTC = ZoneInfo("UTC")


@router.callback_query(F.data.regexp(r"^demo:other:(work|home)$"))
async def demo_send_other(cb: CallbackQuery):
    """По кнопке под демо-аудио отправляем второй демо-транс.

    Требование UX:
    - отправляется только по запросу пользователя
    - без дубликатов (на всякий случай убираем старые demo_send)
    """
    await safe_answer_callback(cb)

    parts = (cb.data or "").split(":")
    if len(parts) != 3:
        return
    _, _, kind = parts
    if kind not in ("work", "home"):
        return

    user_id = int(cb.from_user.id)
    admin_demo_bypass = can_repeat_demo_for_user(user_id)

    sent = demo_sent_kinds(user_id)
    # Если оба демо уже были отправлены — дальше не раздаём бесплатные повторы.
    if not admin_demo_bypass and "work" in sent and "home" in sent:
        return await cb.message.answer(
            "✅ Вы уже получили оба ресурсных демо-транса.\n\n"
            "Если Вы хотите продолжить — пожалуйста, оформите подписку.",
            reply_markup=kb_sales_offer(user_id),
        )
    if not admin_demo_bypass and kind in sent:
        return await cb.message.answer(
            "✅ Этот демо-транс уже был отправлен Вам ранее.\n\n"
            "Если Вы хотите продолжить — пожалуйста, оформите подписку.",
            reply_markup=kb_sales_offer(user_id),
        )

    # На случай, если в очереди остались старые demo_send (или пользователь нажал несколько раз)
    cancel_jobs(user_id, job_types=["demo_send"])  # точечно, не трогаем другие задачи

    run_now = datetime.now(UTC).replace(microsecond=0).isoformat()
    add_job(user_id, "demo_send", run_now, {"kind": kind, "src": "cross"})
    log_event(user_id, "demo_cross_requested", {"kind": kind})

    await cb.message.answer("✅ Хорошо. Сейчас пришлю Вам второй ресурсный демо-транс.")


def _get_demo_sent_at_utc(user_id: int, kind: str, message_id: int) -> str | None:
    """Вернуть sent_at_utc для конкретного демо-сообщения.

    Нужно для детерминированного тайминга оффера "на следующий день" в то же время,
    которое пользователь выбрал при планировании демо.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT sent_at_utc FROM demo_events WHERE user_id=? AND kind=? AND message_id=?",
            (int(user_id), str(kind), int(message_id)),
        ).fetchone()
    return str(row["sent_at_utc"]) if row and row["sent_at_utc"] else None


@router.callback_query(F.data.startswith("demo:ack:"))
async def demo_ack(cb: CallbackQuery):
    await safe_answer_callback(cb)

    # ожидаем: demo:ack:{kind}:{message_id}
    parts = (cb.data or "").split(":")
    if len(parts) != 4:
        return await cb.message.answer(
            "⚠️ Некорректная кнопка демо. Откройте меню → «Демо» и запланируйте демо заново."
        )

    _, _, kind, msgid = parts
    if kind not in ("work", "home"):
        return await cb.message.answer(
            "⚠️ Некорректный тип демо. Откройте меню → «Демо» и запланируйте демо заново."
        )

    try:
        msg_id = int(msgid)
    except (TypeError, ValueError):
        logging.getLogger(__name__).exception("Unhandled exception")
        return await cb.message.answer(
            "⚠️ Некорректный идентификатор демо. Откройте меню → «Демо» и запланируйте демо заново."
        )

    ack_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    ok = record_demo_ack(cb.from_user.id, kind, msg_id, ack_utc)

    if ok:
        # аккуратный апсейл без изменения UX: просто удобные кнопки
        await cb.message.answer(
            "✅ Спасибо! Я отметил, что Вы прослушали демо.\n\n"
            "Если захотите продолжить — можно открыть подписку, подарить доступ другу или посоветовать бота.",
            reply_markup=kb_sales_offer(cb.from_user.id),
        )

        # Микровопрос (не чаще 1 раза/сутки), чтобы аккуратно подстроить сопровождение.
        try:
            q_key = should_offer_micro_question(int(cb.from_user.id))
            if q_key:
                q = get_micro_question(q_key)
                if q:
                    await cb.message.answer(
                        str(q.get("question")),
                        reply_markup=kb_micro_question(str(q.get("key")), list(q.get("options") or [])),
                    )
        except sqlite3.Error:
            logging.getLogger(__name__).exception("Unhandled exception")
        except (KeyError, TypeError, ValueError):
            logging.getLogger(__name__).exception("Unhandled exception")

        # автоворонка после демо
        if not store.is_sub_active(cb.from_user.id):
            t0 = datetime.now(UTC).replace(microsecond=0)

            # AI: выбор профиля сопровождения (soft/standard/urgent) без изменения UX.
            # Это влияет только на тайминги/частоту касаний, но никогда не спрашивает пользователя.
            try:
                from services.ai import choose_funnel_profile, record_funnel_profile
                profile = choose_funnel_profile(int(cb.from_user.id), kind=kind)
                record_funnel_profile(int(cb.from_user.id), profile, meta={"kind": kind, "ack_at_utc": ack_utc})
            except ImportError:
                profile = "standard"
            except (sqlite3.Error, KeyError, TypeError):
                profile = "standard"
            except ValueError:
                profile = "standard"

            # Funnel 2.0: сценарий "не оплатил через 24ч".
            # Идемпотентность гарантируется на этапе исполнения (через funnel_events).
            add_job(
                cb.from_user.id,
                "funnel2_demo_nopay_24h",
                (t0 + timedelta(hours=24)).isoformat(),
                {"kind": kind, "ack_at_utc": ack_utc},
            )

            # Мягкое касание через несколько минут после demo_ack,
            # если человек ещё не открыл тарифы (проверяется в engine).
            add_job(
                cb.from_user.id,
                "funnel_postdemo",
                (t0 + timedelta(minutes=int(settings.FUNNEL_POSTDEMO_MINUTES))).isoformat(),
                {"kind": kind, "ack_at_utc": ack_utc},
            )

            # Дополнительные шаги воронки: детерминированные, управляются профилем.
            # soft — минимум, standard — базовые, urgent — чуть быстрее.
            try:
                if profile in ("standard", "urgent"):
                    nudge_min = 60 if profile == "urgent" else 120
                    deadline_h = max(6, int(settings.FUNNEL_DEADLINE_HOURS) // (2 if profile == "urgent" else 1))
                    lastcall_h = max(12, int(settings.FUNNEL_LASTCALL_HOURS) // (2 if profile == "urgent" else 1))

                    add_job(
                        cb.from_user.id,
                        "funnel_nudge",
                        (t0 + timedelta(minutes=nudge_min)).isoformat(),
                        {"kind": kind},
                    )
                    add_job(
                        cb.from_user.id,
                        "funnel_deadline",
                        (t0 + timedelta(hours=deadline_h)).isoformat(),
                        {"kind": kind},
                    )
                    add_job(
                        cb.from_user.id,
                        "funnel_lastcall",
                        (t0 + timedelta(hours=lastcall_h)).isoformat(),
                        {"kind": kind},
                    )
            except sqlite3.Error:
                logging.getLogger(__name__).exception("Unhandled exception")
            except (ValueError, TypeError, KeyError):
                logging.getLogger(__name__).exception("Unhandled exception")

            # ✅ Тайминг-оффер после демо:
            # 1) через 20 минут после demo_ack
            # 2) на следующий день в то же время, которое пользователь выбрал при планировании демо
            add_job(
                cb.from_user.id,
                "funnel_offer",
                (t0 + timedelta(minutes=20)).isoformat(),
                {"kind": kind, "variant": "after_20m"},
            )

            # Следующий день в то же HH:MM (берём из demo_events.sent_at_utc → local time)
            try:
                tz = ZoneInfo(getattr(settings, "TIMEZONE", "Europe/Moscow"))
                sent_at = _get_demo_sent_at_utc(cb.from_user.id, kind, msg_id)
                if sent_at:
                    sent_local = datetime.fromisoformat(sent_at).astimezone(tz)
                    next_local = datetime.now(tz).replace(
                        hour=sent_local.hour,
                        minute=sent_local.minute,
                        second=0,
                        microsecond=0,
                    ) + timedelta(days=1)
                    next_utc = next_local.astimezone(UTC)
                else:
                    next_utc = t0 + timedelta(days=1)

                add_job(
                    cb.from_user.id,
                    "funnel_offer",
                    next_utc.replace(microsecond=0).isoformat(),
                    {"kind": kind, "variant": "nextday_same_time"},
                )
            except (ValueError, TypeError, ZoneInfoNotFoundError):
                # в крайнем случае не ломаем воронку
                add_job(
                    cb.from_user.id,
                    "funnel_offer",
                    (t0 + timedelta(days=1)).isoformat(),
                    {"kind": kind, "variant": "nextday_fallback"},
                )
            # deadline/lastcall планируются выше (по профилю). Здесь не дублируем.

            log_event(cb.from_user.id, "funnel_scheduled", {"from": "demo_ack"})
    else:
        await cb.message.answer(
            "Я не нашёл запись демо для этой кнопки.\n"
            "Это бывает, если сообщение переслали/удалили.\n\n"
            "Откройте меню → «Демо» и запланируйте демо заново."
        )
