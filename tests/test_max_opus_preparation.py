from __future__ import annotations

from pathlib import Path

import pytest

from services.messenger.max_audio import MaxOpusPreparationError, ensure_max_opus_file


def test_ensure_max_opus_file_keeps_existing_opus(tmp_path: Path) -> None:
    source = tmp_path / "track.opus"
    source.write_bytes(b"opus")

    assert ensure_max_opus_file(source) == source


def test_ensure_max_opus_file_fails_for_missing_source(tmp_path: Path) -> None:
    with pytest.raises(MaxOpusPreparationError):
        ensure_max_opus_file(tmp_path / "missing.mp3")


def test_ensure_max_opus_file_converts_non_opus(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "track.mp3"
    source.write_bytes(b"mp3")
    cache = tmp_path / "cache"
    monkeypatch.setenv("MAX_OPUS_CACHE_DIR", str(cache))

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
