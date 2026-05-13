from __future__ import annotations

import json

from runtime.messenger_max_ui import native_keyboard_attachments
from runtime.messenger_payloads import normalise_messenger_text
from runtime.messenger_vk_ui import vk_score_scale_keyboard_json
from services.messenger.reply_dispatcher import _looks_like_score_scale


POST_AUDIO_SCORE_PROMPT = (
    "✅ Подтвердил аудио №11.\n\n"
    "Теперь оцените состояние после прослушивания.\n\n"
    "Шкала оценки: -10 — стало сильно хуже, 0 — без изменений, +10 — стало сильно лучше."
)


def _max_button_texts(attachments: list[dict]) -> list[str]:
    texts: list[str] = []
    for attachment in attachments:
        for row in attachment.get("payload", {}).get("buttons", []):
            for button in row:
                text = button.get("text")
                if text is not None:
                    texts.append(str(text))
    return texts


def _vk_button_commands(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    commands: list[str] = []
    for row in keyboard["buttons"]:
        for button in row:
            payload = json.loads(button["action"]["payload"])
            commands.append(str(payload["command"]))
    return commands


def test_vk_and_max_done_button_labels_normalize_to_done_command():
    assert normalise_messenger_text("✅ Прослушал") == "done"
    assert normalise_messenger_text("прослушал") == "done"
    assert normalise_messenger_text("готово") == "done"


def test_post_audio_score_prompt_is_detected_as_score_scale_surface():
    assert _looks_like_score_scale(POST_AUDIO_SCORE_PROMPT)


def test_max_post_audio_score_prompt_gets_native_score_scale_attachment():
    attachments = native_keyboard_attachments(POST_AUDIO_SCORE_PROMPT)
    texts = _max_button_texts(attachments)

    assert attachments
    assert "-10" in texts
    assert "0" in texts
    assert "10" in texts


def test_vk_score_scale_keyboard_contains_full_numeric_range():
    commands = _vk_button_commands(vk_score_scale_keyboard_json())

    assert "-10" in commands
    assert "0" in commands
    assert "10" in commands
    assert commands.count("progress") == 1
    assert commands.count("start") == 1
