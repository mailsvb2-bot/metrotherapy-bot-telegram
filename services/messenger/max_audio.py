from __future__ import annotations

"""MAX-native audio preparation.

MAX delivery must send an audio attachment directly in the bot window. To avoid
silent UX degradation, this module prepares a real .opus file before the sender
uploads it to MAX. Link fallback is intentionally handled elsewhere and is
forbidden for MAX.
"""

import hashlib
import os
import re
import subprocess
from pathlib import Path


class MaxOpusPreparationError(RuntimeError):
    pass


def _cache_dir() -> Path:
    root = Path(os.getenv("MAX_OPUS_CACHE_DIR", "data/max_opus_cache"))
    return root if root.is_absolute() else Path.cwd() / root


def _target_path(source: Path) -> Path:
    digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:16]
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.stem).strip("._") or "audio"
    return _cache_dir() / f"{safe_stem}.{digest}.opus"


def ensure_max_opus_file(file_path: Path | str) -> Path:
    """Return a .opus file ready for MAX native audio upload.

    If the source is already .opus, it is used as-is. Otherwise ffmpeg converts
    it into a deterministic cache path. The function fails loudly if conversion
    is impossible, because MAX must not silently fall back to a link.
    """
    source = Path(file_path)
    if not source.exists() or not source.is_file():
        raise MaxOpusPreparationError(f"MAX audio source does not exist: {source}")

    if source.suffix.lower() == ".opus":
        return source

    target = _target_path(source)
    if target.exists() and target.stat().st_size > 0 and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".opus.tmp")
    cmd = [
        os.getenv("FFMPEG_BIN", "ffmpeg"),
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "libopus",
        "-b:a",
        os.getenv("MAX_OPUS_BITRATE", "48k"),
        str(tmp),
    ]

    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=int(os.getenv("MAX_OPUS_CONVERT_TIMEOUT_SEC", "300")),
        )
    except FileNotFoundError as exc:
        raise MaxOpusPreparationError(
            "MAX native .opus delivery requires ffmpeg. Install ffmpeg or provide .opus source files."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MaxOpusPreparationError(f"MAX .opus conversion timed out for {source}") from exc

    if completed.returncode != 0 or not tmp.exists() or tmp.stat().st_size <= 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        details = (completed.stderr or completed.stdout or "").strip()[-500:]
        raise MaxOpusPreparationError(f"MAX .opus conversion failed for {source}: {details}")

    tmp.replace(target)
    return target
