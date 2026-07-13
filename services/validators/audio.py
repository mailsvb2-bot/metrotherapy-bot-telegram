from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from services.validators.base import ValidationError

log = logging.getLogger(__name__)

# Public compatibility surface used by readiness probes and focused tests.
# Runtime directories are resolved dynamically so monkeypatching PROJECT_ROOT or
# setting AUDIO_DIR/DEMO_DIR affects the same checks that live readiness uses.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _audio_validation_skipped() -> bool:
    return (os.getenv("VALIDATOR_SKIP_AUDIO") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _configured_dir(env_name: str, default_relative: str) -> Path:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return PROJECT_ROOT / default_relative
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _demo_dir() -> Path:
    return _configured_dir("DEMO_DIR", "audio/demo")


def _full_dir() -> Path:
    return _configured_dir("AUDIO_DIR", "audio/full")


def _iter_audio_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    exts = {".opus", ".ogg", ".mp3", ".wav", ".m4a"}
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and not path.name.startswith(".")
    ]
    return [path for path in files if path.suffix.lower() in exts]


def validate_demo_audio(strict: bool = True, *, allow_skip: bool = True) -> None:
    if allow_skip and _audio_validation_skipped():
        return
    demo_dir = _demo_dir()
    files = _iter_audio_files(demo_dir)
    names = {path.stem.lower(): path for path in files}

    missing = [kind for kind in ("work", "home") if kind not in names]
    if missing:
        msg = (
            f"Demo audio missing: {missing}. "
            f"Expected work/home audio files in {demo_dir}"
        )
        if strict:
            raise ValidationError(msg)
        log.warning(msg)


def validate_full_audio(strict: bool = True, *, allow_skip: bool = True) -> None:
    if allow_skip and _audio_validation_skipped():
        return
    full_dir = _full_dir()
    files = _iter_audio_files(full_dir)

    bad = [path.name for path in files if not re.match(r"^\d+_", path.name)]
    if bad:
        msg = (
            "Full audio files must start with a numeric prefix and underscore. "
            f"Bad files: {bad}. Folder: {full_dir}"
        )
        if strict:
            raise ValidationError(msg)
        log.warning(msg)

    nums: list[int] = []
    for path in files:
        match = re.match(r"^(\d+)_", path.name)
        if match:
            nums.append(int(match.group(1)))

    if nums:
        has_odd = any(number % 2 == 1 for number in nums)
        has_even = any(number % 2 == 0 for number in nums)
        if not (has_odd and has_even):
            msg = (
                "Full audio numbering must include BOTH odd and even numbers. "
                f"Found numbers: {sorted(set(nums))[:20]} (showing up to 20)."
            )
            if strict:
                raise ValidationError(msg)
            log.warning(msg)
    else:
        msg = f"No usable anchored full audio files found in {full_dir}."
        if strict:
            raise ValidationError(msg)
        log.warning(msg)


def audio_readiness() -> tuple[bool, str | None]:
    """Fail closed against the same configured media directories runtime uses."""

    try:
        validate_demo_audio(strict=True, allow_skip=False)
        validate_full_audio(strict=True, allow_skip=False)
    except ValidationError as exc:
        return False, f"audio:{exc}"
    return True, None
