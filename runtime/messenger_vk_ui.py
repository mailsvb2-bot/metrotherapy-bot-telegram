from __future__ import annotations

import json
from typing import Any


def vk_default_keyboard_json() -> str:
    """Persistent VK keyboard aligned 1:1 with Telegram kb_main()."""
    return json.dumps(
        {
            "one_time": False,
            "inline": False,
            "buttons": [
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "🌿 Попробовать бесплатно",
                            "payload": "{\"command\":\"demo\"}",
                        },
                        "color": "positive",
                    },
                    {
                        "action": {
                            "type": "text",
                            "label": "🔐 Полный маршрут",
                            "payload": "{\"command\":\"full\"}",
                        },
                        "color": "primary",
                    },
                ],
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "💳 Тарифы",
                            "payload": "{\"command\":\"pay\"}",
                        },
                        "color": "primary",
                    },
                    {
                        "action": {
                            "type": "text",
                            "label": "🎁 Подарить",
                            "payload": "{\"command\":\"gift\"}",
                        },
                        "color": "secondary",
                    },
                ],
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "📈 Мой прогресс",
                            "payload": "{\"command\":\"progress\"}",
                        },
                        "color": "primary",
                    },
                    {
                        "action": {
                            "type": "text",
                            "label": "🧠 Настройки",
                            "payload": "{\"command\":\"settings\"}",
                        },
                        "color": "secondary",
                    },
                ],
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "📣 Посоветовать",
                            "payload": "{\"command\":\"share\"}",
                        },
                        "color": "secondary",
                    },
                    {
                        "action": {
                            "type": "text",
                            "label": "🌤 Погода",
                            "payload": "{\"command\":\"weather\"}",
                        },
                        "color": "secondary",
                    },
                ],
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "🎧 Получить аудио",
                            "payload": "{\"command\":\"continue\"}",
                        },
                        "color": "secondary",
                    },
                    {
                        "action": {
                            "type": "text",
                            "label": "✅ Прослушал",
                            "payload": "{\"command\":\"done\"}",
                        },
                        "color": "positive",
                    },
                ],
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def vk_demo_kind_keyboard_json() -> str:
    """VK keyboard for Telegram demo-kind parity."""
    return json.dumps(
        {
            "one_time": False,
            "inline": False,
            "buttons": [
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "1️⃣ Утро / дорога",
                            "payload": "{\"command\":\"demo_work\"}",
                        },
                        "color": "positive",
                    },
                ],
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "2️⃣ Вечер / домой",
                            "payload": "{\"command\":\"demo_home\"}",
                        },
                        "color": "primary",
                    },
                ],
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "⬅️ Назад",
                            "payload": "{\"command\":\"start\"}",
                        },
                        "color": "secondary",
                    },
                ],
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _button(label: str, command: str, color: str = "secondary") -> dict[str, Any]:
    return {
        "action": {
            "type": "text",
            "label": label,
            "payload": json.dumps({"command": command}, ensure_ascii=False),
        },
        "color": color,
    }


def vk_weather_keyboard_json() -> str:
    """VK weather keyboard aligned with Telegram weather entry surface."""
    return json.dumps(
        {
            "one_time": False,
            "inline": False,
            "buttons": [
                [
                    _button("🔄 Обновить погоду", "weather", "primary"),
                    _button("🏙 Изменить город", "weather_city", "secondary"),
                ],
                [_button("⬅️ Меню", "start", "secondary")],
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def vk_weather_city_keyboard_json() -> str:
    """VK keyboard while waiting for city input."""
    return json.dumps(
        {
            "one_time": False,
            "inline": False,
            "buttons": [[_button("⬅️ Меню", "start", "secondary")]],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def vk_score_scale_keyboard_json() -> str:
    """VK keyboard for mood score scale parity."""
    rows: list[list[dict[str, Any]]] = []
    for row in [
        [-10, -9, -8],
        [-7, -6, -5],
        [-4, -3, -2],
        [-1, 0, 1],
        [2, 3, 4],
        [5, 6, 7],
        [8, 9, 10],
    ]:
        rows.append([
            _button(("+" if value > 0 else "") + str(value), str(value), "primary" if value == 0 else "secondary")
            for value in row
        ])
    rows.append([
        _button("📊 Прогресс", "progress", "primary"),
        _button("⬅️ Меню", "start", "secondary"),
    ])
    return json.dumps(
        {"one_time": False, "inline": False, "buttons": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def vk_text_send_kwargs(platform: str, text: str = "") -> dict[str, Any]:
    if platform != "vk":
        return {}
    return {"keyboard_json": vk_default_keyboard_json()}


def with_vk_keyboard(platform: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    if platform != "vk":
        return kwargs
    enriched = dict(kwargs)
    enriched.setdefault("keyboard_json", vk_default_keyboard_json())
    return enriched


def keyboard_for_reply_kind(kind: str | None) -> str | None:
    if kind == "demo_kind":
        return vk_demo_kind_keyboard_json()
    if kind == "score_scale":
        return vk_score_scale_keyboard_json()
    if kind == "weather":
        return vk_weather_keyboard_json()
    if kind == "weather_city":
        return vk_weather_city_keyboard_json()
    return None
