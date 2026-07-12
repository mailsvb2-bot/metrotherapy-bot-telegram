from __future__ import annotations

import logging
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIO_ROOT = PROJECT_ROOT / "audio"


def _configured_dir(env_name: str, default_relative: str) -> Path | None:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _pick_subdir(base: Path, *candidates: str) -> Path:
    """Resolve a content subdirectory across legacy casing/localized layouts."""

    for name in candidates:
        path = base / name
        if path.exists() and path.is_dir():
            return path

    try:
        items = {path.name.lower(): path for path in base.iterdir() if path.is_dir()}
        for name in candidates:
            path = items.get(name.lower())
            if path:
                return path
    except OSError:
        logging.getLogger(__name__).exception("Audio directory scan failed: %s", base)

    return base / (candidates[0] if candidates else "")


DEMO_DIR = _configured_dir("DEMO_DIR", "audio/demo") or _pick_subdir(
    DEFAULT_AUDIO_ROOT,
    "Demo",
    "demo",
    "DEMO",
    "Демо",
    "демо",
)
FULL_DIR = _configured_dir("AUDIO_DIR", "audio/full") or _pick_subdir(
    DEFAULT_AUDIO_ROOT,
    "Full",
    "full",
    "FULL",
    "Полный",
    "полный",
)
# Backward-compatible name: historically AUDIO_DIR meant the audio root.
AUDIO_DIR = DEFAULT_AUDIO_ROOT

EXTS = (".ogg", ".opus", ".mp3", ".wav", ".m4a")
NUM_RE = re.compile(r"(\d+)")


def _num_key(name: str) -> int:
    match = NUM_RE.search(name)
    return int(match.group(1)) if match else 10**9


def _scan(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in EXTS
    ]
    files.sort(key=lambda path: (_num_key(path.name), path.name.lower()))
    return files


class AudioCatalog:
    def get_demo(self) -> list[Path]:
        return _scan(DEMO_DIR)

    def get_full(self) -> list[Path]:
        return _scan(FULL_DIR)
