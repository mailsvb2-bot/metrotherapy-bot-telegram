from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import fast_send_audio as mod


class _DummyConn:
    def commit(self) -> None:
        pass


@contextmanager
def _dummy_get_db():
    yield _DummyConn()


async def _direct_safe(factory):
    return await factory()


class _FakeFSInputFile:
    def __init__(self, path: str):
        self.path = path


@pytest.mark.asyncio
async def test_send_audio_cached_passes_protect_content_for_cached_file_id(monkeypatch, tmp_path):
    calls: list[dict] = []

    class Bot:
        async def send_audio(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(audio=SimpleNamespace(file_id="cached-file-id"))

    monkeypatch.setattr(mod, "get_db", _dummy_get_db)
    monkeypatch.setattr(mod, "get_file_id", lambda _conn, _key: "cached-file-id")
    monkeypatch.setattr(mod, "safe", _direct_safe)

    await mod.send_audio_cached(
        Bot(),
        chat_id=123,
        key="track.mp3",
        file_path=tmp_path / "track.mp3",
        caption="caption",
        protect_content=True,
    )

    assert calls == [
        {
            "chat_id": 123,
            "audio": "cached-file-id",
            "caption": "caption",
            "protect_content": True,
        }
    ]


@pytest.mark.asyncio
async def test_send_audio_cached_passes_protect_content_for_upload_and_caches_file_id(monkeypatch, tmp_path):
    calls: list[dict] = []
    cached: list[tuple[str, str]] = []

    class Bot:
        async def send_audio(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(audio=SimpleNamespace(file_id="new-file-id"))

    def _set_file_id(_conn, key: str, file_id: str) -> None:
        cached.append((key, file_id))

    monkeypatch.setattr(mod, "get_db", _dummy_get_db)
    monkeypatch.setattr(mod, "get_file_id", lambda _conn, _key: None)
    monkeypatch.setattr(mod, "set_file_id", _set_file_id)
    monkeypatch.setattr(mod, "safe", _direct_safe)
    monkeypatch.setattr(mod, "FSInputFile", _FakeFSInputFile)

    file_path = tmp_path / "track.m4a"
    await mod.send_audio_cached(
        Bot(),
        chat_id=456,
        key="track.m4a",
        file_path=file_path,
        caption=None,
        protect_content=True,
    )

    assert calls[0]["chat_id"] == 456
    assert isinstance(calls[0]["audio"], _FakeFSInputFile)
    assert calls[0]["audio"].path == str(Path(file_path))
    assert calls[0]["protect_content"] is True
    assert cached == [("track.m4a", "new-file-id")]
