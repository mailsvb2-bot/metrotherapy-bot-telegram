from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from runtime.messenger_vk_sender import VkBotSender
from services.messenger import audio_delivery, max_audio
from services.messenger.platforms import MessengerPlatform


def test_vk_sender_uploads_prepared_opus_as_audio_message(tmp_path: Path) -> None:
    assert VkBotSender._vk_upload_type_for_audio(tmp_path / "track.mp3") == "doc"
    assert VkBotSender._vk_upload_type_for_audio(tmp_path / "track.opus") == "audio_message"
    assert VkBotSender._vk_upload_type_for_audio(tmp_path / "track.ogg") == "audio_message"


def test_vk_prepare_native_audio_path_converts_non_native_audio(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "track.mp3"
    source.write_bytes(b"mp3")
    prepared = tmp_path / "track.opus"
    prepared.write_bytes(b"opus")
    calls: list[Path] = []

    def fake_prepare(file_path: Path) -> Path:
        calls.append(Path(file_path))
        return prepared

    monkeypatch.setattr(audio_delivery, "ensure_vk_opus_file", fake_prepare)
    item = SimpleNamespace(path=source)

    result = asyncio.run(audio_delivery._prepare_native_audio_path(MessengerPlatform.VK.value, item))

    assert result == prepared
    assert calls == [source]


def test_vk_opus_conversion_keeps_opus_suffix_and_explicit_muxer(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "track.mp3"
    source.write_bytes(b"source")
    cache_dir = tmp_path / "vk-cache"
    captured: dict[str, object] = {}

    monkeypatch.setenv("VK_OPUS_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(max_audio, "_ffmpeg_bin", lambda _platform: "ffmpeg")

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = dict(kwargs)
        output = Path(cmd[-1])
        captured["output"] = output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"valid-opus")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(max_audio.subprocess, "run", fake_run)

    result = max_audio.ensure_vk_opus_file(source)

    cmd = captured["cmd"]
    output = captured["output"]
    assert isinstance(cmd, list)
    assert isinstance(output, Path)
    assert cmd[-3:-1] == ["-f", "opus"]
    assert output.suffix == ".opus"
    assert output.name.endswith(".tmp.opus")
    assert result.suffix == ".opus"
    assert result.read_bytes() == b"valid-opus"
    assert not output.exists()


def test_vk_prepare_native_audio_path_keeps_native_ogg_without_preflight(monkeypatch, tmp_path: Path) -> None:
    native = tmp_path / "missing-but-native.ogg"
    calls: list[Path] = []

    def fake_prepare(file_path: Path) -> Path:
        calls.append(Path(file_path))
        return file_path

    monkeypatch.setattr(audio_delivery, "ensure_vk_opus_file", fake_prepare)
    item = SimpleNamespace(path=native)

    result = asyncio.run(audio_delivery._prepare_native_audio_path(MessengerPlatform.VK.value, item))

    assert result == native
    assert calls == []


def test_max_prepare_native_audio_path_still_uses_max_opus_preparation(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "track.mp3"
    source.write_bytes(b"mp3")
    prepared = tmp_path / "track.opus"
    prepared.write_bytes(b"opus")
    calls: list[Path] = []

    def fake_prepare(file_path: Path) -> Path:
        calls.append(Path(file_path))
        return prepared

    monkeypatch.setattr(audio_delivery, "ensure_max_opus_file", fake_prepare)
    item = SimpleNamespace(path=source)

    result = asyncio.run(audio_delivery._prepare_native_audio_path(MessengerPlatform.MAX.value, item))

    assert result == prepared
    assert calls == [source]
