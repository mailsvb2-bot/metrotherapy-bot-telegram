from __future__ import annotations

"""Messenger-native Opus audio preparation.

MAX and VK delivery must send audio attachments directly in the bot window. To
avoid silent UX degradation, this module prepares a real .opus file before the
sender uploads it to the provider. Link fallback is intentionally handled by the
caller only when the product flow explicitly allows it.
"""

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path


class MessengerOpusPreparationError(RuntimeError):
    pass


class MaxOpusPreparationError(MessengerOpusPreparationError):
    pass


class VkOpusPreparationError(MessengerOpusPreparationError):
    pass


def _clean_platform(platform: str) -> str:
    value = str(platform or "").strip().lower()
    return value if value in {"max", "vk"} else "messenger"


def _error_cls(platform: str) -> type[MessengerOpusPreparationError]:
    clean = _clean_platform(platform)
    if clean == "max":
        return MaxOpusPreparationError
    if clean == "vk":
        return VkOpusPreparationError
    return MessengerOpusPreparationError


def _env_name(platform: str, suffix: str) -> str:
    return f"{_clean_platform(platform).upper()}_OPUS_{suffix}"


def _cache_dir(platform: str) -> Path:
    clean = _clean_platform(platform)
    root = Path(
        os.getenv(_env_name(clean, "CACHE_DIR"))
        or os.getenv("MESSENGER_OPUS_CACHE_DIR")
        or f"data/{clean}_opus_cache"
    )
    return root if root.is_absolute() else Path.cwd() / root


def _bitrate(platform: str) -> str:
    return (
        os.getenv(_env_name(platform, "BITRATE"))
        or os.getenv("MESSENGER_OPUS_BITRATE")
        or "48k"
    ).strip()


def _timeout_sec(platform: str) -> int:
    raw = (
        os.getenv(_env_name(platform, "CONVERT_TIMEOUT_SEC"))
        or os.getenv("MESSENGER_OPUS_CONVERT_TIMEOUT_SEC")
        or "300"
    ).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 300


def _ffmpeg_bin(platform: str) -> str:
    raw = (os.getenv("FFMPEG_BIN") or "ffmpeg").strip()
    if not raw:
        raw = "ffmpeg"

    candidate = Path(raw)
    if candidate.is_absolute():
        if candidate.exists() and candidate.is_file():
            return str(candidate)
        raise _error_cls(platform)(f"{platform.upper()} ffmpeg executable is not found: {candidate}")

    resolved = shutil.which(raw)
    if resolved:
        return resolved

    raise _error_cls(platform)(
        f"{platform.upper()} native .opus delivery requires ffmpeg. "
        "Install ffmpeg or set FFMPEG_BIN to an absolute executable path."
    )


def _target_path(source: Path, *, platform: str) -> Path:
    digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:16]
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.stem).strip("._") or "audio"
    return _cache_dir(platform) / f"{safe_stem}.{digest}.opus"


def _native_ready_suffixes(platform: str) -> set[str]:
    clean = _clean_platform(platform)
    if clean == "vk":
        # VK sender uploads both .opus and .ogg as `audio_message`; forcing every
        # .ogg through ffmpeg breaks tests and can block already-native audio.
        return {".opus", ".ogg"}
    return {".opus"}


def ensure_messenger_opus_file(file_path: Path | str, *, platform: str) -> Path:
    """Return a file ready for native messenger audio upload.

    If the source is already accepted by the target provider as native audio, it
    is used as-is. Otherwise ffmpeg converts it into a deterministic .opus cache
    path. The function fails loudly if conversion is impossible, because native
    messenger audio must not silently degrade into a broken document upload.
    """
    clean_platform = _clean_platform(platform)
    error_cls = _error_cls(clean_platform)
    source = Path(file_path)
    if not source.exists() or not source.is_file():
        raise error_cls(f"{clean_platform.upper()} audio source does not exist: {source}")

    if source.suffix.lower() in _native_ready_suffixes(clean_platform):
        return source

    target = _target_path(source, platform=clean_platform)
    if target.exists() and target.stat().st_size > 0 and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".opus.tmp")
    cmd = [
        _ffmpeg_bin(clean_platform),
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
        _bitrate(clean_platform),
        str(tmp),
    ]

    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_timeout_sec(clean_platform),
        )
    except FileNotFoundError as exc:
        raise error_cls(
            f"{clean_platform.upper()} native .opus delivery requires ffmpeg. "
            "Install ffmpeg or set FFMPEG_BIN to an absolute executable path."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise error_cls(f"{clean_platform.upper()} .opus conversion timed out for {source}") from exc

    if completed.returncode != 0 or not tmp.exists() or tmp.stat().st_size <= 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        details = (completed.stderr or completed.stdout or "").strip()[-500:]
        raise error_cls(f"{clean_platform.upper()} .opus conversion failed for {source}: {details}")

    tmp.replace(target)
    return target


def ensure_max_opus_file(file_path: Path | str) -> Path:
    return ensure_messenger_opus_file(file_path, platform="max")


def ensure_vk_opus_file(file_path: Path | str) -> Path:
    return ensure_messenger_opus_file(file_path, platform="vk")
