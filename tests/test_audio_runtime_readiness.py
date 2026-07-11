from __future__ import annotations

from services.validators import audio


def _write_audio_tree(root, *, demo: tuple[str, ...], full: tuple[str, ...]):
    demo_dir = root / "audio" / "demo"
    full_dir = root / "audio" / "full"
    demo_dir.mkdir(parents=True)
    full_dir.mkdir(parents=True)
    for name in demo:
        (demo_dir / name).write_bytes(b"audio")
    for name in full:
        (full_dir / name).write_bytes(b"audio")


def test_audio_readiness_ignores_validator_skip_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("VALIDATOR_SKIP_AUDIO", "1")

    ready, error = audio.audio_readiness()

    assert ready is False
    assert error is not None
    assert error.startswith("audio:Demo audio missing")


def test_audio_readiness_accepts_complete_runtime_content(tmp_path, monkeypatch):
    _write_audio_tree(
        tmp_path,
        demo=("work.ogg", "home.ogg"),
        full=("1_work.ogg", "2_home.ogg"),
    )
    monkeypatch.setattr(audio, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("VALIDATOR_SKIP_AUDIO", "1")

    assert audio.audio_readiness() == (True, None)


def test_audio_readiness_rejects_full_series_without_odd_even_pair(tmp_path, monkeypatch):
    _write_audio_tree(
        tmp_path,
        demo=("work.ogg", "home.ogg"),
        full=("2_first.ogg", "4_second.ogg"),
    )
    monkeypatch.setattr(audio, "PROJECT_ROOT", tmp_path)

    ready, error = audio.audio_readiness()

    assert ready is False
    assert error is not None
    assert "BOTH odd and even" in error
