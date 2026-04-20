from __future__ import annotations

import logging
import compileall
import os
import re
from pathlib import Path
from typing import Iterable

import sqlite3

from services.db import get_connection, DB_PATH
from core.paths import ROOT as PROJECT_ROOT

log = logging.getLogger(__name__)


from services.validators.base import ValidationError

def _iter_audio_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    exts = {".opus", ".ogg", ".mp3", ".wav", ".m4a"}
    files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
    return [p for p in files if p.suffix.lower() in exts]
def validate_demo_audio(strict: bool = True) -> None:
    demo_dir = PROJECT_ROOT / "audio" / "demo"
    files = _iter_audio_files(demo_dir)
    names = {p.stem.lower(): p for p in files}

    missing = [k for k in ("work", "home") if k not in names]
    if missing:
        msg = f"Demo audio missing: {missing}. Expected files like work.opus and home.opus in {demo_dir}"
        if strict:
            raise ValidationError(msg)
        log.warning(msg)
def validate_full_audio(strict: bool = True) -> None:
    full_dir = PROJECT_ROOT / "audio" / "full"
    files = _iter_audio_files(full_dir)

    # Allow any leading integer length, but require '<number>_' prefix for anchoring.
    bad = [p.name for p in files if not re.match(r"^\d+_", p.name)]
    if bad:
        msg = (
            "Full audio files must start with a numeric prefix and underscore (e.g. 001_work.opus). "
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
                "Full audio numbering must include BOTH odd (work) and even (home) numbers. "
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
