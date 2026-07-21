from __future__ import annotations

from pathlib import Path

import pytest

from core import runtime_paths
from services import prewarm
from services.validators import audio
from services.validators.base import ValidationError


def _ogg(path: Path, *, size: int = 256) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"OggS" + b"\0" * max(0, size - 4))


def test_production_runtime_paths_are_outside_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    release = tmp_path / "runtime" / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("METRO_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("METRO_WRITABLE_ROOT", raising=False)
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    monkeypatch.delenv("PREWARM_MARKER_PATH", raising=False)

    writable = runtime_paths.writable_root()
    mpl = runtime_paths.matplotlib_cache_dir()
    marker = runtime_paths.prewarm_marker_path()

    assert writable == (tmp_path / "state").resolve()
    assert release not in mpl.parents
    assert release not in marker.parents
    assert mpl.is_dir()
    assert marker.parent.is_dir()


def test_prewarm_marker_tracks_media_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    marker = tmp_path / "state" / "prewarm" / "audio.done"
    audio_file = tmp_path / "audio" / "work.ogg"
    _ogg(audio_file)
    monkeypatch.setenv("PREWARM_MARKER_PATH", str(marker))

    first = prewarm._content_fingerprint([audio_file])
    prewarm._mark_done(first)
    assert prewarm._already_done(first)

    audio_file.write_bytes(audio_file.read_bytes() + b"changed")
    second = prewarm._content_fingerprint([audio_file])
    assert second != first
    assert not prewarm._already_done(second)


def test_audio_validator_accepts_real_container_headers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    full = tmp_path / "full"
    _ogg(demo / "work.ogg")
    _ogg(demo / "home.ogg")
    _ogg(full / "1_work.ogg")
    _ogg(full / "2_home.ogg")
    monkeypatch.setenv("DEMO_DIR", str(demo))
    monkeypatch.setenv("AUDIO_DIR", str(full))
    monkeypatch.setenv("AUDIO_VALIDATION_MIN_BYTES", "64")

    audio.validate_demo_audio(strict=True, allow_skip=False)
    audio.validate_full_audio(strict=True, allow_skip=False)
    assert audio.audio_readiness() == (True, None)


def test_audio_validator_rejects_empty_or_malformed_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    full = tmp_path / "full"
    demo.mkdir()
    full.mkdir()
    (demo / "work.ogg").write_bytes(b"")
    _ogg(demo / "home.ogg")
    (full / "1_work.ogg").write_bytes(b"not-an-ogg" + b"x" * 100)
    _ogg(full / "2_home.ogg")
    monkeypatch.setenv("DEMO_DIR", str(demo))
    monkeypatch.setenv("AUDIO_DIR", str(full))
    monkeypatch.setenv("AUDIO_VALIDATION_MIN_BYTES", "64")

    with pytest.raises(ValidationError, match="too_small"):
        audio.validate_demo_audio(strict=True, allow_skip=False)
    with pytest.raises(ValidationError, match="container_header_mismatch"):
        audio.validate_full_audio(strict=True, allow_skip=False)
    ok, problem = audio.audio_readiness()
    assert ok is False
    assert problem and problem.startswith("audio:")
