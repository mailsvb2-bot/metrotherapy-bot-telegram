from __future__ import annotations

from pathlib import Path

import pytest

from services.messenger.max_audio import (
    MaxOpusPreparationError,
    VkOpusPreparationError,
    ensure_max_opus_file,
    ensure_messenger_opus_file,
    ensure_vk_opus_file,
)


def test_ensure_max_opus_file_keeps_existing_opus(tmp_path: Path) -> None:
    source = tmp_path / "track.opus"
    source.write_bytes(b"opus")

    assert ensure_max_opus_file(source) == source


def test_ensure_max_opus_file_keeps_supported_mp3(tmp_path: Path) -> None:
    source = tmp_path / "track.mp3"
    source.write_bytes(b"mp3")

    assert ensure_max_opus_file(source) == source


def test_ensure_max_opus_file_keeps_supported_wav(tmp_path: Path) -> None:
    source = tmp_path / "track.wav"
    source.write_bytes(b"wav")

    assert ensure_max_opus_file(source) == source


def test_ensure_max_opus_file_fails_for_missing_source(tmp_path: Path) -> None:
    with pytest.raises(MaxOpusPreparationError):
        ensure_max_opus_file(tmp_path / "missing.mp3")


def test_ensure_max_opus_file_converts_unsupported_format(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "track.rawaudio"
    source.write_bytes(b"raw")
    cache = tmp_path / "cache"
    monkeypatch.setenv("MAX_OPUS_CACHE_DIR", str(cache))
    monkeypatch.setattr("services.messenger.max_audio._ffmpeg_bin", lambda platform: "ffmpeg")

    def fake_run(cmd, check, stdout, stderr, text, timeout):
        out = Path(cmd[-1])
        out.write_bytes(b"converted-opus")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("services.messenger.max_audio.subprocess.run", fake_run)

    prepared = ensure_max_opus_file(source)

    assert prepared.suffix == ".opus"
    assert prepared.exists()
    assert prepared.read_bytes() == b"converted-opus"


def test_ensure_vk_opus_file_fails_for_missing_source(tmp_path: Path) -> None:
    with pytest.raises(VkOpusPreparationError):
        ensure_vk_opus_file(tmp_path / "missing.mp3")


def test_ensure_vk_opus_file_converts_non_opus_with_vk_cache(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "vk track.mp3"
    source.write_bytes(b"mp3")
    vk_cache = tmp_path / "vk-cache"
    monkeypatch.setenv("VK_OPUS_CACHE_DIR", str(vk_cache))
    monkeypatch.setattr("services.messenger.max_audio._ffmpeg_bin", lambda platform: "ffmpeg")

    def fake_run(cmd, check, stdout, stderr, text, timeout):
        out = Path(cmd[-1])
        out.write_bytes(b"vk-converted-opus")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("services.messenger.max_audio.subprocess.run", fake_run)

    prepared = ensure_vk_opus_file(source)

    assert prepared.suffix == ".opus"
    assert prepared.parent == vk_cache
    assert prepared.exists()
    assert prepared.read_bytes() == b"vk-converted-opus"


def test_ensure_messenger_opus_file_separates_provider_caches(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "track.rawaudio"
    source.write_bytes(b"raw")
    max_cache = tmp_path / "max-cache"
    vk_cache = tmp_path / "vk-cache"
    monkeypatch.setenv("MAX_OPUS_CACHE_DIR", str(max_cache))
    monkeypatch.setenv("VK_OPUS_CACHE_DIR", str(vk_cache))
    monkeypatch.setattr("services.messenger.max_audio._ffmpeg_bin", lambda platform: "ffmpeg")

    def fake_run(cmd, check, stdout, stderr, text, timeout):
        out = Path(cmd[-1])
        out.write_bytes(b"converted")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("services.messenger.max_audio.subprocess.run", fake_run)

    max_prepared = ensure_messenger_opus_file(source, platform="max")
    vk_prepared = ensure_messenger_opus_file(source, platform="vk")

    assert max_prepared.parent == max_cache
    assert vk_prepared.parent == vk_cache
    assert max_prepared.name == vk_prepared.name
