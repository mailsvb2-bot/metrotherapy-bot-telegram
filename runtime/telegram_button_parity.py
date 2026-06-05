from __future__ import annotations

import json
from typing import Any


def _button_text(button: Any) -> str:
    return str(getattr(button, "text", "") or "")


def _button_callback(button: Any) -> str:
    return str(getattr(button, "callback_data", "") or "")


def telegram_button_rows(markup: Any) -> list[list[tuple[str, str]]]:
    rows = getattr(markup, "inline_keyboard", None) or []
    return [
        [(_button_text(button), _button_callback(button)) for button in row]
        for row in rows
    ]


def max_attachment_from_telegram(markup: Any) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    for row in telegram_button_rows(markup):
        out_row: list[dict[str, Any]] = []
        for text, callback in row:
            button: dict[str, Any] = {"type": "message", "text": text}
            if callback:
                button["payload"] = {"command": callback}
            out_row.append(button)
        rows.append(out_row)
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def vk_keyboard_from_telegram(markup: Any, *, inline: bool = False, color: str = "secondary") -> str:
    rows: list[list[dict[str, Any]]] = []
    for row in telegram_button_rows(markup):
        out_row: list[dict[str, Any]] = []
        for text, callback in row:
            out_row.append(
                {
                    "action": {
                        "type": "text",
                        "label": text,
                        "payload": json.dumps({"command": callback}, ensure_ascii=False),
                    },
                    "color": color,
                }
            )
        rows.append(out_row)
    return json.dumps(
        {"one_time": False, "inline": inline, "buttons": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    )
