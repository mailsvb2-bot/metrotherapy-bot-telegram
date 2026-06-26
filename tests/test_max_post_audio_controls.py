from __future__ import annotations

from runtime import messenger_max_ui as max_ui


def _button_texts(attachment: dict) -> list[str]:
    return [button["text"] for row in attachment["payload"]["buttons"] for button in row]


def _button_commands(attachment: dict) -> list[str]:
    return [str((button.get("payload") or {}).get("command") or "") for row in attachment["payload"]["buttons"] for button in row]


def test_max_post_audio_controls_include_done_progress_history_and_menu() -> None:
    attachment = max_ui.post_audio_attachment()

    assert _button_commands(attachment) == ["done", "progress", "history", "start"]
    assert _button_texts(attachment) == ["✅ Прослушал", "📊 Прогресс", "🧾 История", "⬅️ Меню"]
