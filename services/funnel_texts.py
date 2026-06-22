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
            "Если хочется продолжить — можно выбрать подписку (тарифы). "
            "А если Вы уже послушали — нажмите кнопку «Прослушал» в сообщении с демо (для статистики)."
        )

    if step == "postdemo":
        return (
            f"{hello_line}✨ Небольшой шаг дальше\n\n"
            f"Если демо {kline} было полезным — загляните в тарифы: там можно включить полный доступ и расписание. "
            "Можно также подарить подписку другу или посоветовать бот — это часто помогает начать вместе."
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
            "Чтобы трансформация ощущалась глубже — лучше слушать регулярно, по расписанию. "
            "Если хотите продолжить — выберите тариф."
        )

    if step == "lastcall":
        return (
            f"{hello_line}🔥 Последнее мягкое напоминание\n\n"
            "Если Вы чувствуете, что это Ваш формат — просто выберите тариф. "
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

    custom = get_active_copy(step, variant)
    if custom:
        return custom

    # Приоритет — кастомный текст из БД (AI-копирайтер/маркетинг)
    custom = get_active_copy(step, variant)
    if custom:
        return custom

    if step == "offer":
        if variant == "B":
            return (
                f"{hello_line}✨ Если Вам хочется продолжения\n\n"
                f"Демо {kline} — это только первый шаг. В подписке Вы получаете регулярные сессии по расписанию, "
                "и именно регулярность даёт ощущение устойчивости.\n\n"
                "Если Вам подходит — выберите подписку (тариф) прямо сейчас."
            )
        # A
        return (
            f"{hello_line}✨ Продолжение (мягко и по делу)\n\n"
            f"Если демо {kline} было Вам полезным — в полной версии Вы получаете регулярные сессии по расписанию. "
            "Это и даёт эффект: не разово, а как привычка к ясности и спокойствию.\n\n"
            "Если Вы хотите продолжить — выберите подписку (тариф)."
        )

    if step == "offer_nextday":
        if variant == "B":
            return (
                f"{hello_line}🕊 Небольшое напоминание\n\n"
                "Если вчерашнее демо Вам откликнулось — Вы можете продолжить системно: в подписке есть регулярные сессии "
                "по расписанию.\n\n"
                "Если Вам подходит — выберите тариф."
            )
        # A
        return (
            f"{hello_line}🕊 Вчера Вы пробовали демо — спасибо, что дали себе этот опыт.\n\n"
            "Если Вы хотите продолжить мягко и системно — в подписке есть регулярные сессии по расписанию. "
            "Именно регулярность даёт накопительный эффект.\n\n"
            "Если Вам подходит — выберите тариф."
        )

    return funnel_text(step, kind=kind, hour=hour)
