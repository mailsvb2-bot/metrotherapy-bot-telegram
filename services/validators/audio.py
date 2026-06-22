from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from core.paths import ROOT as PROJECT_ROOT
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


def validate_demo_audio(strict: bool = True) -> None:
    if _audio_validation_skipped():
        return
    demo_dir = PROJECT_ROOT / "audio" / "demo"
    files = _iter_audio_files(demo_dir)
    names = {p.stem.lower(): p for p in files}

    missing = [k for k in ("work", "home") if k not in names]
    if missing:
        msg = f"Demo audio missing: {missing}. Expected work/home audio files in {demo_dir}"
        if strict:
            raise ValidationError(msg)
        log.warning(msg)


def validate_full_audio(strict: bool = True) -> None:
    if _audio_validation_skipped():
        return
    full_dir = PROJECT_ROOT / "audio" / "full"
    files = _iter_audio_files(full_dir)

    bad = [p.name for p in files if not re.match(r"^\d+_", p.name)]
    if bad:
        msg = (
            "Full audio files must start with a numeric prefix and underscore. "
            f"Bad files: {bad}. Folder: {full_dir}"
        )
        if strict:
            raise ValidationError(msg)
        log.warning(msg)

    nums: list[int] = []
    for p in files:
        m = re.match(r"^(\d+)_", p.name)
        if m:
            nums.append(int(m.group(1)))

    if nums:
        has_odd = any(n % 2 == 1 for n in nums)
        has_even = any(n % 2 == 0 for n in nums)
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
