from __future__ import annotations


from typing import Literal

from services.funnel_copies import get_active_copy


Kind = Literal["work", "home", "both"]
Step = Literal[
    "nudge",
    "postdemo",
    "offer",
    "offer_nextday",
    "deadline",
    "lastcall",
]


Variant = Literal["A", "B"]


def _daypart(hour: int | None) -> str:
    if hour is None:
        return ""
    if 5 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 17:
        return "day"
    if 18 <= hour <= 23:
        return "evening"
    return "night"


def _hello(hour: int | None) -> str:
    p = _daypart(hour)
    if p == "morning":
        return "Доброе утро"
    if p == "day":
        return "Добрый день"
    if p == "evening":
        return "Добрый вечер"
    if p == "night":
        return "Доброй ночи"
    return ""


def _kind_line(kind: Kind) -> str:
    if kind == "work":
        return "про утренний ритм «на работу»"
    if kind == "home":
        return "про вечерний ритм «домой»"
    return "про Ваш ритм"


def funnel_text(step: Step, *, kind: Kind = "both", hour: int | None = None) -> str:
    """Детерминированные тексты автоворонки.

    Персонализация:
      - kind: work/home/both (по какому демо пришёл человек)
      - hour: час последней активности в локальном TZ (утро/день/вечер/ночь)
    """

    hello = _hello(hour)
    hello_line = (hello + "!\n\n") if hello else ""
    kline = _kind_line(kind)

    # Маркетинг может переопределять тексты без изменения кода (таблица funnel_copies).
    custom = get_active_copy(step, "-")
    if custom:
        return custom

    if step == "nudge":
        return (
            f"{hello_line}💬 Как ощущения после демо {kline}?\n\n"
            "Если хочется продолжить — можно выбрать пакет практик и открыть полный маршрут. "
            "А если Вы уже послушали — нажмите кнопку «Прослушал» в сообщении с демо (для статистики)."
        )

    if step == "postdemo":
        return (
            f"{hello_line}✨ Небольшой шаг дальше\n\n"
            f"Если демо {kline} было полезным — загляните в пакеты практик: там можно открыть полный маршрут и расписание. "
            "Можно также подарить пакет практик другу или посоветовать бот — это часто помогает начать вместе."
        )

    if step == "offer":
        # Базовая версия (A). Для A/B используйте funnel_text_ab().
        return funnel_text_ab("offer", "A", kind=kind, hour=hour)

    if step == "offer_nextday":
        # Базовая версия (A). Для A/B используйте funnel_text_ab().
        return funnel_text_ab("offer_nextday", "A", kind=kind, hour=hour)

    if step == "deadline":
        return (
            f"{hello_line}🕊 Напоминание\n\n"
            "Чтобы эффект ощущался глубже, практики лучше слушать регулярно и в безопасной обстановке. "
            "Для продолжения выберите подходящий пакет практик."
        )

    if step == "lastcall":
        return (
            f"{hello_line}🔥 Последнее мягкое напоминание\n\n"
            "Если Вы чувствуете, что это Ваш формат, выберите подходящий пакет практик. "
            "Я не буду надоедать: это финальное сообщение из этой серии."
        )

    # safety fallback
    return ""


def funnel_text_ab(step: Step, variant: Variant, *, kind: Kind = "both", hour: int | None = None) -> str:
    """A/B тексты оффера.

    Важно:
      - только для offer/offer_nextday. Для остальных шагов возвращает funnel_text().
      - тексты детерминированные: выбор варианта делается снаружи.
    """

    hello = _hello(hour)
    hello_line = (hello + "!\n\n") if hello else ""
    kline = _kind_line(kind)

    # Приоритет — кастомный текст из БД (AI-копирайтер/маркетинг).
    custom = get_active_copy(step, variant)
    if custom:
        return custom

    if step == "offer":
        if variant == "B":
            return (
                f"{hello_line}✨ Если Вам хочется продолжения\n\n"
                f"Демо {kline} — это только первый шаг. В полном маршруте Вы получаете регулярные практики по расписанию, "
                "и именно регулярность помогает сформировать устойчивый ритм.\n\n"
                "Если Вам подходит — выберите пакет практик."
            )
        # A
        return (
            f"{hello_line}✨ Продолжение — мягко и по делу\n\n"
            f"Если демо {kline} было Вам полезным, полный маршрут даёт регулярные практики по расписанию. "
            "Это не разовый сеанс, а возможность выстроить привычный ритм.\n\n"
            "Для продолжения выберите пакет практик."
        )

    if step == "offer_nextday":
        if variant == "B":
            return (
                f"{hello_line}🕊 Небольшое напоминание\n\n"
                "Если вчерашнее демо Вам откликнулось, можно продолжить системно: полный маршрут включает регулярные практики "
                "по расписанию.\n\n"
                "Если Вам подходит — выберите пакет практик."
            )
        # A
        return (
            f"{hello_line}🕊 Вчера Вы пробовали демо — спасибо, что дали себе этот опыт.\n\n"
            "Если хотите продолжить мягко и системно, полный маршрут включает регулярные практики по расписанию. "
            "Именно регулярность помогает сформировать устойчивый ритм.\n\n"
            "Если Вам подходит — выберите пакет практик."
        )

    return funnel_text(step, kind=kind, hour=hour)
