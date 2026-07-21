from __future__ import annotations

import asyncio
import builtins
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from services import cache as cache_module
from services import prewarm


def test_cache_ttl_and_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(cache_module.time, "time", lambda: now[0])
    cache = cache_module.Cache()

    assert cache.get("missing") is None
    cache.set("forever", {"value": 1})
    assert cache.get("forever") == {"value": 1}

    cache.set("ttl", "first", ttl=10)
    assert cache.get("ttl") == "first"
    now[0] = 111.0
    assert cache.get("ttl") is None
    assert "ttl" not in cache._d

    cache.set("ttl", "second", ttl=-1)
    now[0] = 1000.0
    assert cache.get("ttl") == "second"
    cache.set("ttl", "replacement", ttl=0)
    assert cache.get("ttl") == "replacement"


def test_marker_helpers_success_and_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / ".prewarm_done"
    monkeypatch.setattr(prewarm, "_marker_path", lambda: marker)
    monkeypatch.setattr(prewarm.time, "time", lambda: 123.9)
    assert prewarm._already_done() is False
    prewarm._mark_done()
    assert marker.read_text(encoding="utf-8") == "123"
    assert prewarm._already_done() is True

    class BrokenPath:
        def exists(self) -> bool:
            raise OSError("exists")

        def write_text(self, *_args: Any, **_kwargs: Any) -> None:
            raise OSError("write")

    monkeypatch.setattr(prewarm, "_marker_path", lambda: BrokenPath())
    assert prewarm._already_done() is False
    prewarm._mark_done()


def test_admin_chat_id_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prewarm, "settings", SimpleNamespace(PREWARM_CHAT_ID=" 42 ", ADMIN_IDS="1,2"))
    assert prewarm._admin_chat_id() == 42

    monkeypatch.setattr(prewarm, "settings", SimpleNamespace(PREWARM_CHAT_ID="invalid", ADMIN_IDS="bad; ; 7,8"))
    assert prewarm._admin_chat_id() == 7

    monkeypatch.setattr(prewarm, "settings", SimpleNamespace(PREWARM_CHAT_ID=55, ADMIN_IDS=""))
    assert prewarm._admin_chat_id() == 55

    monkeypatch.setattr(prewarm, "settings", SimpleNamespace(PREWARM_CHAT_ID="", ADMIN_IDS="bad,none"))
    assert prewarm._admin_chat_id() is None

    monkeypatch.setattr(prewarm, "settings", SimpleNamespace(PREWARM_CHAT_ID="", ADMIN_IDS=""))
    assert prewarm._admin_chat_id() is None


class FakeCatalog:
    demo: list[Path] = []
    full: list[Path] = []
    demo_error: BaseException | None = None
    full_error: BaseException | None = None

    def get_demo(self) -> list[Path]:
        if self.demo_error is not None:
            raise self.demo_error
        return list(self.demo)

    def get_full(self) -> list[Path]:
        if self.full_error is not None:
            raise self.full_error
        return list(self.full)


class FakeBot:
    def __init__(
        self,
        *,
        voice_id: str | None = "voice-id",
        audio_id: str | None = "audio-id",
        fail_names: set[str] | None = None,
    ) -> None:
        self.voice_id = voice_id
        self.audio_id = audio_id
        self.fail_names = fail_names or set()
        self.voice_calls: list[tuple[Any, ...]] = []
        self.audio_calls: list[tuple[Any, ...]] = []

    async def send_voice(self, chat_id: int, **kwargs: Any) -> Any:
        path = Path(kwargs["voice"])
        self.voice_calls.append((chat_id, kwargs))
        if path.name in self.fail_names:
            raise OSError("voice transport")
        return SimpleNamespace(voice=SimpleNamespace(file_id=self.voice_id) if self.voice_id else None)

    async def send_audio(self, chat_id: int, **kwargs: Any) -> Any:
        path = Path(kwargs["audio"])
        self.audio_calls.append((chat_id, kwargs))
        if path.name in self.fail_names:
            raise asyncio.TimeoutError("audio transport")
        return SimpleNamespace(audio=SimpleNamespace(file_id=self.audio_id) if self.audio_id else None)


def configure_prewarm(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    already_done: bool = False,
    chat_id: int | None = 7,
) -> tuple[list[tuple[Path, str, str]], list[bool]]:
    monkeypatch.setattr(prewarm, "settings", SimpleNamespace(PREWARM_ENABLED=enabled))
    monkeypatch.setattr(prewarm, "_already_done", lambda: already_done)
    monkeypatch.setattr(prewarm, "_admin_chat_id", lambda: chat_id)
    monkeypatch.setattr(prewarm, "AudioCatalog", FakeCatalog)
    monkeypatch.setattr(prewarm, "FSInputFile", lambda path: Path(path))
    monkeypatch.setattr(prewarm, "get_cached_file_id", lambda _path, _kind: None)
    saved: list[tuple[Path, str, str]] = []
    monkeypatch.setattr(
        prewarm,
        "save_cached_file_id",
        lambda path, kind, file_id: saved.append((Path(path), kind, file_id)),
    )
    marked: list[bool] = []
    monkeypatch.setattr(prewarm, "_mark_done", lambda: marked.append(True))

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(prewarm.asyncio, "sleep", no_sleep)
    FakeCatalog.demo = []
    FakeCatalog.full = []
    FakeCatalog.demo_error = None
    FakeCatalog.full_error = None
    return saved, marked


@pytest.mark.asyncio
async def test_prewarm_early_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    saved, marked = configure_prewarm(monkeypatch, enabled=False)
    await prewarm.prewarm_audio_cache(FakeBot())
    assert saved == [] and marked == []

    saved, marked = configure_prewarm(monkeypatch, already_done=True)
    await prewarm.prewarm_audio_cache(FakeBot())
    assert saved == [] and marked == []

    saved, marked = configure_prewarm(monkeypatch, chat_id=None)
    await prewarm.prewarm_audio_cache(FakeBot())
    assert saved == [] and marked == []


@pytest.mark.asyncio
async def test_prewarm_success_deduplicates_and_marks_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved, marked = configure_prewarm(monkeypatch)
    voice = tmp_path / "voice.ogg"
    audio = tmp_path / "track.mp3"
    voice.write_bytes(b"voice")
    audio.write_bytes(b"audio")
    FakeCatalog.demo = [voice, audio]
    FakeCatalog.full = [voice]
    bot = FakeBot()

    await prewarm.prewarm_audio_cache(bot)

    assert len(bot.voice_calls) == 1
    assert len(bot.audio_calls) == 1
    assert (voice, "voice", "voice-id") in saved
    assert (audio, "audio", "audio-id") in saved
    assert marked == [True]


@pytest.mark.asyncio
async def test_prewarm_cached_file_is_skipped_and_sweep_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved, marked = configure_prewarm(monkeypatch)
    audio = tmp_path / "cached.mp3"
    audio.write_bytes(b"audio")
    FakeCatalog.demo = [audio]
    monkeypatch.setattr(prewarm, "get_cached_file_id", lambda _path, _kind: "cached-id")
    bot = FakeBot()

    await prewarm.prewarm_audio_cache(bot)

    assert bot.audio_calls == []
    assert saved == []
    assert marked == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "transport", "no_file_id", "catalog"])
async def test_prewarm_retryable_failures_do_not_mark_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    _saved, marked = configure_prewarm(monkeypatch)
    audio = tmp_path / "track.mp3"
    if mode != "missing":
        audio.write_bytes(b"audio")
    FakeCatalog.demo = [audio]
    bot = FakeBot(
        audio_id=None if mode == "no_file_id" else "audio-id",
        fail_names={audio.name} if mode == "transport" else set(),
    )
    if mode == "catalog":
        FakeCatalog.demo = []
        FakeCatalog.demo_error = OSError("catalog mount")

    await prewarm.prewarm_audio_cache(bot)

    assert marked == []


@pytest.mark.asyncio
async def test_prewarm_full_catalog_failure_still_processes_demo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved, marked = configure_prewarm(monkeypatch)
    voice = tmp_path / "voice.opus"
    voice.write_bytes(b"voice")
    FakeCatalog.demo = [voice]
    FakeCatalog.full_error = OSError("full unavailable")

    await prewarm.prewarm_audio_cache(FakeBot())

    assert saved == [(voice, "voice", "voice-id")]
    assert marked == []


@pytest.mark.asyncio
async def test_prewarm_matplotlib_success_and_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.charts as charts

    calls: list[str] = []
    monkeypatch.setattr(charts, "_ensure_mpl", lambda: calls.append("ok"))

    async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(prewarm.asyncio, "to_thread", direct_to_thread)
    await prewarm.prewarm_matplotlib_cache()
    assert calls == ["ok"]

    monkeypatch.setattr(charts, "_ensure_mpl", lambda: (_ for _ in ()).throw(OSError("mpl")))
    await prewarm.prewarm_matplotlib_cache()


@pytest.mark.asyncio
async def test_prewarm_matplotlib_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def import_hook(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "services.charts":
            raise ImportError("charts unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_hook)
    await prewarm.prewarm_matplotlib_cache()
