from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from runtime.messenger_vk_sender import VkBotSender
from services.messenger import audio_delivery
from services.messenger.platforms import MessengerPlatform


def test_vk_sender_uploads_prepared_opus_as_audio_message(tmp_path: Path) -> None:
    assert VkBotSender._vk_upload_type_for_audio(tmp_path / "track.mp3") == "doc"
    assert VkBotSender._vk_upload_type_for_audio(tmp_path / "track.opus") == "audio_message"
    assert VkBotSender._vk_upload_type_for_audio(tmp_path / "track.ogg") == "audio_message"


def test_vk_prepare_native_audio_path_uses_opus_preparation(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "track.mp3"
    source.write_bytes(b"mp3")
    prepared = tmp_path / "track.opus"
    prepared.write_bytes(b"opus")
    calls: list[tuple[Path, str]] = []

    def fake_prepare(file_path: Path, *, platform: str) -> Path:
        calls.append((Path(file_path), platform))
        return prepared

    monkeypatch.setattr(audio_delivery, "ensure_messenger_opus_file", fake_prepare)
    item = SimpleNamespace(path=source)

    result = asyncio.run(audio_delivery._prepare_native_audio_path(MessengerPlatform.VK.value, item))

    assert result == prepared
    assert calls == [(source, MessengerPlatform.VK.value)]


def test_max_prepare_native_audio_path_still_uses_opus_preparation(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "track.mp3"
    source.write_bytes(b"mp3")
    prepared = tmp_path / "track.opus"
    prepared.write_bytes(b"opus")
    calls: list[tuple[Path, str]] = []

    def fake_prepare(file_path: Path, *, platform: str) -> Path:
        calls.append((Path(file_path), platform))
        return prepared

    monkeypatch.setattr(audio_delivery, "ensure_messenger_opus_file", fake_prepare)
    item = SimpleNamespace(path=source)

    result = asyncio.run(audio_delivery._prepare_native_audio_path(MessengerPlatform.MAX.value, item))

    assert result == prepared
    assert calls == [(source, MessengerPlatform.MAX.value)]
