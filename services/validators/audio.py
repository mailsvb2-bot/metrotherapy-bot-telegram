from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from services.catalog import DEMO_DIR, FULL_DIR
from services.validators.base import ValidationError

log = logging.getLogger(__name__)


def _audio_validation_skipped() -> bool:
    return (os.getenv("VALIDATOR_SKIP_AUDIO") or "").strip().lower() in {"1", "true", "yes", "on"}


def _iter_audio_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    exts = {".opus", ".ogg", ".mp3", ".wav", ".m4a"}
    files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
    return [p for p in files if p.suffix.lower() in exts]


def validate_demo_audio(strict: bool = True, *, allow_skip: bool = True) -> None:
    if allow_skip and _audio_validation_skipped():
        return
    files = _iter_audio_files(DEMO_DIR)
    names = {p.stem.lower(): p for p in files}

    missing = [kind for kind in ("work", "home") if kind not in names]
    if missing:
        msg = f"Demo audio missing: {missing}. Expected work/home audio files in {DEMO_DIR}"
        if strict:
            raise ValidationError(msg)
        log.warning(msg)


def validate_full_audio(strict: bool = True, *, allow_skip: bool = True) -> None:
    if allow_skip and _audio_validation_skipped():
        return
    files = _iter_audio_files(FULL_DIR)

    bad = [p.name for p in files if not re.match(r"^\d+_", p.name)]
    if bad:
        msg = (
            "Full audio files must start with a numeric prefix and underscore. "
            f"Bad files: {bad}. Folder: {FULL_DIR}"
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
        msg = f"No usable anchored full audio files found in {FULL_DIR}."
        if strict:
            raise ValidationError(msg)
        log.warning(msg)


def audio_readiness() -> tuple[bool, str | None]:
    """Fail closed against the same configured media directories used by runtime."""

    try:
        validate_demo_audio(strict=True, allow_skip=False)
        validate_full_audio(strict=True, allow_skip=False)
    except ValidationError as exc:
        return False, f"audio:{exc}"
    return True, None
