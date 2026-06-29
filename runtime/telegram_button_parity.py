from __future__ import annotations

import json
from typing import Any

VK_MAX_BUTTONS_PER_ROW = 5
VK_MAX_BUTTON_ROWS = 6

CALLBACK_COMMANDS = {
    "sub:menu": "pay",
    "gift:menu": "gift",
    "settings:menu": "settings",
    "settings:state": "progress",
    "share:menu": "share",
    "weather:show": "weather",
    "weather:city": "weather_city",
    "menu:main": "start",
    "back": "start",
    "demo_kind_work": "demo_work",
    "demo_kind_home": "demo_home",
    "demo": "demo",
    "full": "full",
}


def _button_text(button: Any) -> str:
    return str(getattr(button, "text", "") or "")


def _button_callback(button: Any) -> str:
    return str(getattr(button, "callback_data", "") or "")


def telegram_button_rows(markup: Any) -> list[list[tuple[str, str]]]:
    rows = getattr(markup, "inline_keyboard", None) or []
    return [[(_button_text(button), _button_callback(button)) for button in row] for row in rows]


def _chunks(row: list[Any], size: int) -> list[list[Any]]:
    return [row[i : i + size] for i in range(0, len(row), size)]


def _pack_vk_rows(rows: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    if len(rows) <= VK_MAX_BUTTON_ROWS:
        return rows
    flat = [button for row in rows for button in row]
    packed = _chunks(flat, VK_MAX_BUTTONS_PER_ROW)
    if len(packed) <= VK_MAX_BUTTON_ROWS:
        return packed
    # Last-resort defensive packing: keep all buttons and preserve order, while
    # allowing the final row to carry overflow rather than sending a keyboard VK
    # is known to reject because of row count. Normal project keyboards should
    # not reach this branch after row-width contracts are applied.
    return packed[: VK_MAX_BUTTON_ROWS - 1] + [[button for row in packed[VK_MAX_BUTTON_ROWS - 1 :] for button in row]]


def _score_from_mood_callback(callback: str) -> str | None:
    raw = str(callback or "").strip()
    if not raw.startswith("mood:"):
        return None
    parts = raw.split(":")
    if len(parts) < 4 or parts[1] not in {"pre", "post"}:
        return None
    try:
        value = int(parts[-1])
    except ValueError:
        return None
    return str(value) if -10 <= value <= 10 else None


def canonical_button_command(callback: str) -> str:
    raw = str(callback or "").strip()
    score = _score_from_mood_callback(raw)
    if score is not None:
        return score
    if raw.startswith("mood:done"):
        return "done"
    if raw.startswith("settings:time:"):
        return "time"
    if raw == "settings:ref":
        return "share"
    if raw == "settings:platform:menu":
        return "settings"
    if raw == "settings:delivery:channels":
        return "time"
    if raw.startswith("settings:delivery:slot:set:"):
        parts = raw.split(":")
        if len(parts) >= 6:
            return f"channel {parts[4]} {parts[5]}"
    if raw.startswith("settings:delivery:slot:"):
        parts = raw.split(":")
        if len(parts) >= 4:
            return f"channel {parts[3]}"
    return CALLBACK_COMMANDS.get(raw, raw)


def _max_text_for(text: str, callback: str) -> str:
    score = _score_from_mood_callback(callback)
    return score if score is not None else text


def _max_command_for(callback: str) -> str:
    score = _score_from_mood_callback(callback)
    return f"score:{score}" if score is not None else canonical_button_command(callback)


def max_attachment_from_telegram(markup: Any) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    for row in telegram_button_rows(markup):
        out_row: list[dict[str, Any]] = []
        for text, callback in row:
            button: dict[str, Any] = {"type": "message", "text": _max_text_for(text, callback)}
            command = _max_command_for(callback)
            if command:
                button["payload"] = {"command": command}
            out_row.append(button)
        rows.append(out_row)
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def vk_keyboard_from_telegram(markup: Any, *, inline: bool = True, color: str = "secondary") -> str:
    rows: list[list[dict[str, Any]]] = []
    for row in telegram_button_rows(markup):
        for chunk in _chunks(row, VK_MAX_BUTTONS_PER_ROW):
            out_row: list[dict[str, Any]] = []
            for text, callback in chunk:
                out_row.append(
                    {
                        "action": {
                            "type": "callback",
                            "label": text,
                            "payload": json.dumps({"command": canonical_button_command(callback)}, ensure_ascii=False),
                        },
                        "color": color,
                    }
                )
            rows.append(out_row)
    rows = _pack_vk_rows(rows)
    payload = {"inline": bool(inline), "buttons": rows}
    if not inline:
        payload["one_time"] = False
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
