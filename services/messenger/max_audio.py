from __future__ import annotations

"""MAX-native audio preparation.

MAX must receive audio as a native bot-window attachment. MAX upload rejects the
project's source .opus files in practice, while supported audio formats include
m4a/mp3/wav. Prepare an AAC/M4A file before upload and fail loudly when
conversion is impossible.
"""

import hashlib
import os
import re
import subprocess
from pathlib import Path


class MaxOpusPreparationError(RuntimeError):
    """Backward-compatible error name for older callers/tests."""


class MaxAudioPreparationError(MaxOpusPreparationError):
    pass


def _cache_dir() -> Path:
    root = Path(os.getenv("MAX_AUDIO_CACHE_DIR", os.getenv("MAX_OPUS_CACHE_DIR", "data/max_audio_cache")))
    return root if root.is_absolute() else Path.cwd() / root


def _target_path(source: Path) -> Path:
    digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:16]
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.stem).strip("._") or "audio"
    return _cache_dir() / f"{safe_stem}.{digest}.m4a"


def ensure_max_audio_file(file_path: Path | str) -> Path:
    """Return an audio file suitable for MAX native audio upload.

    MAX currently rejects the project's .opus sources during upload. The sender
    therefore prepares a deterministic AAC/M4A copy for MAX only. Telegram/VK
    can continue using the original .opus files.
    """
    source = Path(file_path)
    if not source.exists() or not source.is_file():
        raise MaxAudioPreparationError(f"MAX audio source does not exist: {source}")

    if source.suffix.lower() == ".m4a":
        return source

    target = _target_path(source)
    if target.exists() and target.stat().st_size > 0 and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp.m4a")
    cmd = [
        os.getenv("FFMPEG_BIN", "ffmpeg"),
        "-y",
        "-i",
        str(source),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        os.getenv("MAX_M4A_BITRATE", "96k"),
        "-f",
        "mp4",
        str(tmp),
    ]

    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=int(os.getenv("MAX_AUDIO_CONVERT_TIMEOUT_SEC", os.getenv("MAX_OPUS_CONVERT_TIMEOUT_SEC", "300"))),
        )
    except FileNotFoundError as exc:
        raise MaxAudioPreparationError(
            "MAX native audio delivery requires ffmpeg. Install ffmpeg or provide .m4a source files."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MaxAudioPreparationError(f"MAX audio conversion timed out for {source}") from exc

    if completed.returncode != 0 or not tmp.exists() or tmp.stat().st_size <= 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        details = (completed.stderr or completed.stdout or "").strip()[-500:]
        raise MaxAudioPreparationError(f"MAX audio conversion failed for {source}: {details}")

    tmp.replace(target)
    return target


def ensure_max_opus_file(file_path: Path | str) -> Path:
    """Compatibility alias.

    Older code imports this name, but MAX-native delivery now prepares M4A/AAC
    because MAX upload rejects project .opus files.
    """
    return ensure_max_audio_file(file_path)
