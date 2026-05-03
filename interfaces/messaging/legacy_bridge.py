from __future__ import annotations

"""Compatibility bridge from existing text_ui replies to CanonicalResponse.

This is a migration seam, not a new business engine. The existing funnel/text UI
still decides what to say. This module only describes the channel-neutral button
surface that renderers can convert to Telegram/MAX/VK payloads.
"""

from collections.abc import Iterable

from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse
from services.messenger.text_ui import MessengerReply


def _btn(text: str, action: str) -> CanonicalButton:
    return CanonicalButton(text=text, action=action, kind="command")


def _main_buttons() -> tuple[tuple[CanonicalButton, ...], ...]:
    return (
        (_btn("🌿 Попробовать бесплатно", "demo"), _btn("🔐 Полный маршрут", "full")),
        (_btn("💳 Тарифы", "pay"), _btn("🎁 Подарить", "gift")),
        (_btn("📈 Мой прогресс", "progress"), _btn("🧠 Настройки", "settings")),
        (_btn("📣 Посоветовать", "share"), _btn("🌤 Погода", "weather")),
    )


def _demo_buttons() -> tuple[tuple[CanonicalButton, ...], ...]:
    return (
        (_btn("🚗 Практика на утро / дорогу", "demo_work"),),
        (_btn("🌙 Практика на вечер / домой", "demo_home"),),
        (_btn("⬅️ Назад", "start"),),
    )


def _score_buttons() -> tuple[tuple[CanonicalButton, ...], ...]:
    values = list(range(-10, 11))
    rows: list[tuple[CanonicalButton, ...]] = []
    for index in range(0, len(values), 7):
        rows.append(tuple(
            _btn(f"{value:+d}" if value != 0 else "0", str(value))
            for value in values[index:index + 7]
        ))
    rows.append((_btn("⬅️ Меню", "start"),))
    return tuple(rows)


def _full_route_buttons() -> tuple[tuple[CanonicalButton, ...], ...]:
    return (
        (_btn("🎧 Получить аудио", "continue"), _btn("✅ Прослушал", "done")),
        (_btn("⬅️ Меню", "start"),),
    )


def _weather_buttons() -> tuple[tuple[CanonicalButton, ...], ...]:
    return (
        (_btn("🔄 Обновить погоду", "weather"), _btn("🏙 Изменить город", "weather_city")),
        (_btn("⬅️ Меню", "start"),),
    )


def _after_audio_buttons() -> tuple[tuple[CanonicalButton, ...], ...]:
    return (
        (_btn("✅ Прослушал", "done"), _btn("🔁 Повторить аудио", "repeat")),
        (_btn("⬅️ Меню", "start"),),
    )


def messenger_reply_to_canonical(reply: MessengerReply) -> CanonicalResponse:
    meta = dict(reply.meta or {})
    text = reply.text or ""
    keyboard_kind = meta.get("vk_keyboard") or meta.get("keyboard")

    buttons: tuple[tuple[CanonicalButton, ...], ...] = ()
    stripped = text.lstrip()
    lowered = stripped.casefold()

    if keyboard_kind == "demo_kind" or stripped.startswith("🌿 Бесплатная практика"):
        buttons = _demo_buttons()
    elif keyboard_kind == "score_scale" or "шкала оценки" in lowered or "оцените состояние сейчас" in lowered:
        buttons = _score_buttons()
    elif keyboard_kind == "weather" or stripped.startswith("🌤 Погода"):
        buttons = _weather_buttons()
    elif keyboard_kind == "weather_city" or stripped.startswith("🏙 Напишите название города"):
        buttons = ((_btn("⬅️ Меню", "start"),),)
    elif stripped.startswith("🔐 Полный маршрут"):
        buttons = _full_route_buttons()
    elif stripped.startswith("Главное меню") or "главное меню" in lowered:
        buttons = _main_buttons()
    elif "после оплаты вернитесь сюда" in lowered or "аудио придёт" in lowered or "аудио:" in lowered:
        buttons = _after_audio_buttons()

    return CanonicalResponse(text=text, buttons=buttons, meta={"legacy_reply_kind": reply.kind, **meta})


def messenger_replies_to_canonical(replies: Iterable[MessengerReply]) -> list[CanonicalResponse]:
    return [messenger_reply_to_canonical(reply) for reply in replies]
