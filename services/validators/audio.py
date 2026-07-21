from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from services.audio_asset_integrity import validate_release_assets
from services.validators.base import ValidationError

log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AUDIO_EXTENSIONS = {".opus", ".ogg", ".mp3", ".wav", ".m4a"}


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


def _minimum_audio_bytes() -> int:
    raw = (os.getenv("AUDIO_VALIDATION_MIN_BYTES") or "1024").strip()
    try:
        return max(64, min(int(raw), 10 * 1024 * 1024))
    except (TypeError, ValueError):
        return 1024


def _iter_audio_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in _AUDIO_EXTENSIONS
    ]
    return sorted(files, key=lambda path: path.name.casefold())


def _header_matches_container(path: Path, header: bytes) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".ogg", ".opus"}:
        return header.startswith(b"OggS")
    if suffix == ".wav":
        return len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if suffix == ".m4a":
        return b"ftyp" in header[:32]
    if suffix == ".mp3":
        if header.startswith(b"ID3"):
            return True
        return len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
    return False


def _file_problem(path: Path) -> str | None:
    try:
        size = int(path.stat().st_size)
    except OSError as exc:
        return f"stat_failed:{type(exc).__name__}"
    if size < _minimum_audio_bytes():
        return f"too_small:{size}"
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
    except OSError as exc:
        return f"read_failed:{type(exc).__name__}"
    if not _header_matches_container(path, header):
        return "container_header_mismatch"
    return None


def _invalid_files(files: list[Path]) -> list[str]:
    invalid: list[str] = []
    for path in files:
        problem = _file_problem(path)
        if problem:
            invalid.append(f"{path.name}:{problem}")
    return invalid


def _fail_or_warn(message: str, *, strict: bool) -> None:
    if strict:
        raise ValidationError(message)
    log.warning(message)


def _production_runtime() -> bool:
    return (os.getenv("APP_ENV") or "dev").strip().lower() in {
        "prod",
        "production",
        "stage",
        "staging",
    }


def validate_audio_asset_integrity(strict: bool = True) -> None:
    """Tie runtime media bytes to the content-addressed release pointer."""

    pointer = PROJECT_ROOT / ".audio-assets.json"
    require_versioned = _production_runtime() or pointer.exists()
    try:
        validate_release_assets(PROJECT_ROOT, require_versioned=require_versioned)
    except (OSError, ValueError) as exc:
        _fail_or_warn(f"Versioned audio asset integrity failed: {exc}", strict=strict)


def validate_demo_audio(strict: bool = True, *, allow_skip: bool = True) -> None:
    if allow_skip and _audio_validation_skipped():
        return
    demo_dir = _demo_dir()
    files = _iter_audio_files(demo_dir)
    names = {path.stem.lower(): path for path in files}

    missing = [kind for kind in ("work", "home") if kind not in names]
    if missing:
        _fail_or_warn(
            f"Demo audio missing: {missing}. Expected work/home audio files in {demo_dir}",
            strict=strict,
        )
        if not strict:
            return

    invalid = _invalid_files(files)
    if invalid:
        _fail_or_warn(
            f"Demo audio files are empty, unreadable or malformed: {invalid}. Folder: {demo_dir}",
            strict=strict,
        )


def validate_full_audio(strict: bool = True, *, allow_skip: bool = True) -> None:
    if allow_skip and _audio_validation_skipped():
        return
    full_dir = _full_dir()
    files = _iter_audio_files(full_dir)
    if not files:
        _fail_or_warn(f"No usable anchored full audio files found in {full_dir}.", strict=strict)
        return

    bad_names = [path.name for path in files if not re.match(r"^\d+_", path.name)]
    if bad_names:
        _fail_or_warn(
            "Full audio files must start with a numeric prefix and underscore. "
            f"Bad files: {bad_names}. Folder: {full_dir}",
            strict=strict,
        )

    invalid = _invalid_files(files)
    if invalid:
        _fail_or_warn(
            f"Full audio files are empty, unreadable or malformed: {invalid}. Folder: {full_dir}",
            strict=strict,
        )

    numbers = [
        int(match.group(1))
        for path in files
        if (match := re.match(r"^(\d+)_", path.name)) is not None
    ]
    has_odd = any(number % 2 == 1 for number in numbers)
    has_even = any(number % 2 == 0 for number in numbers)
    if not (has_odd and has_even):
        _fail_or_warn(
            "Full audio numbering must include BOTH odd and even numbers. "
            f"Found numbers: {sorted(set(numbers))[:20]} (showing up to 20).",
            strict=strict,
        )


def audio_readiness() -> tuple[bool, str | None]:
    """Fail closed against the same configured media directories runtime uses."""
    try:
        validate_audio_asset_integrity(strict=True)
        validate_demo_audio(strict=True, allow_skip=False)
        validate_full_audio(strict=True, allow_skip=False)
    except ValidationError as exc:
        return False, f"audio:{exc}"
    return True, None
