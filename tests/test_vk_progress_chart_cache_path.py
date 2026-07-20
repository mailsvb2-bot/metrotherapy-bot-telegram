from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import services.messenger.progress_charts as charts


class _Cursor:
    def fetchall(self):
        return [
            {
                "id": 42,
                "day": "2026-07-01",
                "slot": "morning",
                "kind": "work",
                "anchor_id": 1,
                "pre_score": 1,
                "post_score": 2,
                "audio_sent": 1,
            }
        ]


class _Conn:
    def execute(self, *_args, **_kwargs):
        return _Cursor()


@contextmanager
def _fake_db():
    yield _Conn()


def test_vk_progress_chart_reuses_existing_stable_cache(monkeypatch, tmp_path: Path):
    user_id = 987654321
    out_dir = tmp_path / "cache" / "metrotherapy_vk_charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    cached_path = out_dir / f"progress_{user_id}_42_1.png"
    cached_path.write_bytes(b"cached-png")

    old_mtime = 1_700_000_000
    os.utime(cached_path, (old_mtime, old_mtime))

    monkeypatch.setattr(charts, "DATA_DIR", tmp_path)
    monkeypatch.setattr(charts, "get_db_ro", _fake_db)

    result = charts.build_vk_mood_progress_chart_path(user_id)

    assert result == cached_path.resolve()
    assert cached_path.read_bytes() == b"cached-png"
    assert cached_path.stat().st_mtime == old_mtime
