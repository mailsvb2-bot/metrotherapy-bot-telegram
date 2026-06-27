from __future__ import annotations

import json

from runtime.messenger_senders import MaxBotSender
from runtime.messenger_vk_ui import (
    VK_MAX_BUTTONS_PER_ROW,
    VK_MAX_INLINE_SCORE_BUTTONS,
    VK_SCORE_BUTTON_VALUES,
    vk_score_scale_keyboard_json,
)
from services.mood_text_flow import parse_score_text


EXPECTED_SCORES = list(range(-10, 11))
EXPECTED_VK_BUTTON_SCORES = list(VK_SCORE_BUTTON_VALUES)


def _max_score_values() -> list[int]:
    attachment = MaxBotSender._score_scale_attachment()
    values: list[int] = []
    for row in attachment["payload"]["buttons"]:
        for button in row:
            text = str(button["text"])
            try:
                values.append(int(text))
            except ValueError:
                pass
    return values


def _vk_score_keyboard() -> dict:
    return json.loads(vk_score_scale_keyboard_json())


def _vk_score_values() -> list[int]:
    keyboard = _vk_score_keyboard()
    values: list[int] = []
    for row in keyboard["buttons"]:
        for button in row:
            action = button["action"]
            payload = json.loads(action["payload"])
            command = str(payload["command"])
            try:
                values.append(int(command))
            except ValueError:
                pass
    return values


def test_score_parser_accepts_every_telegram_mood_value() -> None:
    for score in EXPECTED_SCORES:
        assert parse_score_text(str(score)) == score
        if score > 0:
            assert parse_score_text(f"+{score}") == score


def test_vk_score_scale_contains_safe_anchor_values_once() -> None:
    assert _vk_score_values() == EXPECTED_VK_BUTTON_SCORES


def test_vk_score_scale_rows_fit_vk_keyboard_limits() -> None:
    keyboard = _vk_score_keyboard()
    rows = keyboard["buttons"]
    assert all(len(row) <= VK_MAX_BUTTONS_PER_ROW for row in rows)
    assert sum(len(row) for row in rows) <= VK_MAX_INLINE_SCORE_BUTTONS


def test_max_score_scale_contains_every_value_once() -> None:
    assert _max_score_values() == EXPECTED_SCORES
