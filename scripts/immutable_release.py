from __future__ import annotations

"""Atomic immutable-release switching for production deployment.

A release directory is complete only when its `.release.json` marker matches the
40-character Git commit used as the directory name and the release contains its
own Python interpreter and entrypoint. Runtime switching mutates only symlinks;
release contents and their pinned audio asset sets are never changed during
rollback.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from services.audio_asset_integrity import validate_release_assets

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_MARKER = ".release.json"
_ALLOWED_LOCK_FILES = frozenset({"requirements.txt", "requirements-py313.txt"})


@dataclass(frozen=True)
class ReleaseInfo:
    sha: str
    path: str
    python: str
    lock_file: str
    lock_sha256: str
    tree_sha256: str
    built_at_utc: str
    audio_asset_dir: str = ""
    audio_asset_sha256: str = ""
    audio_asset_file_count: int = 0


@dataclass(frozen=True)
class SwitchResult:
    current: ReleaseInfo
    previous: ReleaseInfo | None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _require_sha(value: str) -> str:
    sha = str(value or "").strip()
    if not _SHA_RE.fullmatch(sha):
        raise ValueError("release SHA must be one lowercase 40-character Git commit")
    return sha


def _load_marker(path: Path) -> dict[str, Any]:
    marker = path / _MARKER
    if not marker.is_file():
        raise ValueError(f"release marker is missing: {marker}")
    try:
        loaded = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"release marker is invalid: {marker}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"release marker must be an object: {marker}")
    return loaded


def _digest_entry(
    digest: Any,
    *,
    relative: str,
    kind: bytes,
    mode: int,
    payload: bytes,
) -> None:
    digest.update(relative.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    digest.update(kind)
    digest.update(f"{mode:o}".encode("ascii"))
    digest.update(b"\0")
    digest.update(payload)
    digest.update(b"\0")


def release_tree_sha256(path: str | Path) -> str:
    """Hash the root and all release entries except the self-referential marker.

    The digest includes relative paths, file type, permission bits, symlink
    targets and regular-file bytes. It therefore detects dependency, source,
    sealed audio-pointer or service-readability mutation while remaining stable
    after publication. External audio bytes are verified separately against that
    immutable pointer.
    """

    root = Path(path).resolve(strict=True)
    digest = hashlib.sha256()
    root_metadata = root.lstat()
    _digest_entry(
        digest,
        relative=".",
        kind=b"D",
        mode=stat.S_IMODE(root_metadata.st_mode),
        payload=b"",
    )
    entries = sorted(
        (item for item in root.rglob("*") if item.relative_to(root).as_posix() != _MARKER),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for item in entries:
        relative = item.relative_to(root).as_posix()
        metadata = item.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if item.is_symlink():
            kind = b"L"
            payload = os.readlink(item).encode("utf-8", errors="surrogateescape")
        elif item.is_dir():
            kind = b"D"
            payload = b""
        elif item.is_file():
            kind = b"F"
            hasher = hashlib.sha256()
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            payload = hasher.digest()
        else:
            raise ValueError(f"unsupported release entry: {item}")
        _digest_entry(
            digest,
            relative=relative,
            kind=kind,
            mode=mode,
            payload=payload,
        )
    return digest.hexdigest()


def validate_release(path: str | Path) -> ReleaseInfo:
    release = Path(path).resolve(strict=True)
    if not release.is_dir():
        raise ValueError(f"release path is not a directory: {release}")
    marker = _load_marker(release)
    sha = _require_sha(str(marker.get("sha") or ""))
    if release.name != sha:
        raise ValueError(f"release directory name does not match marker SHA: {release}")

    python_path = release / ".venv" / "bin" / "python"
    if not python_path.is_file() or not os.access(python_path, os.X_OK):
        raise ValueError(f"release Python is missing or not executable: {python_path}")
    if not (release / "main.py").is_file():
        raise ValueError(f"release entrypoint is missing: {release / 'main.py'}")

    lock_file = str(marker.get("lock_file") or "").strip()
    lock_sha256 = str(marker.get("lock_sha256") or "").strip()
    tree_sha256 = str(marker.get("tree_sha256") or "").strip()
    built_at = str(marker.get("built_at_utc") or "").strip()
    lock_relative = Path(lock_file)
    if (
        lock_file not in _ALLOWED_LOCK_FILES
        or lock_relative.is_absolute()
        or len(lock_relative.parts) != 1
    ):
        raise ValueError(f"release dependency lock path is not canonical: {lock_file or '<empty>'}")
    lock_path = release / lock_relative
    if not lock_path.is_file():
        raise ValueError(f"release dependency lock is missing: {lock_file}")
    if not re.fullmatch(r"[0-9a-f]{64}", lock_sha256):
        raise ValueError("release lock SHA-256 is invalid")
    if hashlib.sha256(lock_path.read_bytes()).hexdigest() != lock_sha256:
        raise ValueError("release dependency lock content changed after build")
    if not re.fullmatch(r"[0-9a-f]{64}", tree_sha256):
        raise ValueError("release tree SHA-256 is invalid")
    if release_tree_sha256(release) != tree_sha256:
        raise ValueError("release tree changed after immutable publication")
    if not built_at:
        raise ValueError("release build timestamp is missing")

    audio = validate_release_assets(release, require_versioned=False)
    if audio is not None:
        marker_dir = str(marker.get("shared_audio_dir") or "").strip()
        marker_sha = str(marker.get("audio_asset_sha256") or "").strip()
        try:
            marker_count = int(marker.get("audio_asset_file_count"))
        except (TypeError, ValueError) as exc:
            raise ValueError("release audio asset file count is invalid") from exc
        if marker_dir and Path(marker_dir).resolve(strict=False) != Path(audio.asset_dir):
            raise ValueError("release marker audio directory does not match sealed asset pointer")
        if marker_sha and marker_sha != audio.asset_sha256:
            raise ValueError("release marker audio digest does not match sealed asset pointer")
        if marker_count != audio.file_count:
            raise ValueError("release marker audio file count does not match sealed asset pointer")

    return ReleaseInfo(
        sha=sha,
        path=str(release),
        python=str(python_path),
        lock_file=lock_file,
        lock_sha256=lock_sha256,
        tree_sha256=tree_sha256,
        built_at_utc=built_at,
        audio_asset_dir=audio.asset_dir if audio is not None else "",
        audio_asset_sha256=audio.asset_sha256 if audio is not None else "",
        audio_asset_file_count=audio.file_count if audio is not None else 0,
    )


def resolve_link(link: str | Path, *, required: bool = False) -> ReleaseInfo | None:
    path = Path(link)
    if not path.is_symlink():
        if required:
            raise ValueError(f"release link is missing: {path}")
        return None
    try:
        target = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"release link is dangling: {path}") from exc
    return validate_release(target)


def _atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{link.name}.",
        dir=link.parent,
        delete=True,
    ) as handle:
        temp_path = Path(handle.name)
    temp_path.symlink_to(target)
    try:
        os.replace(temp_path, link)
    finally:
        temp_path.unlink(missing_ok=True)


def switch_release(
    *,
    release_dir: str | Path,
    current_link: str | Path,
    previous_link: str | Path,
) -> SwitchResult:
    candidate = validate_release(release_dir)
    current_path = Path(current_link)
    previous_path = Path(previous_link)
    old_current = resolve_link(current_path)

    if old_current is not None and Path(old_current.path) != Path(candidate.path):
        _atomic_symlink(Path(old_current.path), previous_path)
    _atomic_symlink(Path(candidate.path), current_path)
    return SwitchResult(current=candidate, previous=old_current)


def rollback_release(*, current_link: str | Path, previous_link: str | Path) -> SwitchResult:
    current_path = Path(current_link)
    previous_path = Path(previous_link)
    old_current = resolve_link(current_path, required=True)
    rollback_target = resolve_link(previous_path, required=True)
    assert old_current is not None
    assert rollback_target is not None

    # resolve_link validates both the release tree and pinned external audio bytes
    # before either symlink is changed.
    _atomic_symlink(Path(rollback_target.path), current_path)
    _atomic_symlink(Path(old_current.path), previous_path)
    return SwitchResult(current=rollback_target, previous=old_current)


def write_deployment_proof(
    *,
    proof_file: str | Path,
    current_link: str | Path,
    previous_link: str | Path,
    production_gate: str,
    health_url: str,
    readiness_url: str,
) -> dict[str, Any]:
    current = resolve_link(current_link, required=True)
    previous = resolve_link(previous_link)
    assert current is not None
    payload: dict[str, Any] = {
        "proof_version": 2,
        "proved_at_utc": _utc_now_iso(),
        "deployed_sha": current.sha,
        "release_dir": current.path,
        "release_python": current.python,
        "dependency_lock": current.lock_file,
        "dependency_lock_sha256": current.lock_sha256,
        "release_tree_sha256": current.tree_sha256,
        "audio_asset_dir": current.audio_asset_dir,
        "audio_asset_sha256": current.audio_asset_sha256,
        "audio_asset_file_count": current.audio_asset_file_count,
        "previous_sha": previous.sha if previous is not None else "",
        "previous_release_dir": previous.path if previous is not None else "",
        "previous_release_tree_sha256": previous.tree_sha256 if previous is not None else "",
        "previous_audio_asset_dir": previous.audio_asset_dir if previous is not None else "",
        "previous_audio_asset_sha256": previous.audio_asset_sha256 if previous is not None else "",
        "previous_audio_asset_file_count": previous.audio_asset_file_count if previous is not None else 0,
        "production_gate": str(production_gate),
        "health_url": str(health_url),
        "readiness_url": str(readiness_url),
    }
    destination = Path(proof_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return payload


def _print(value: Any) -> None:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage immutable Metrotherapy runtime releases")
    sub = parser.add_subparsers(dest="command", required=True)

    digest = sub.add_parser("tree-digest")
    digest.add_argument("release_dir")

    validate = sub.add_parser("validate")
    validate.add_argument("release_dir")

    inspect = sub.add_parser("inspect")
    inspect.add_argument("link")
    inspect.add_argument("--required", action="store_true")

    switch = sub.add_parser("switch")
    switch.add_argument("--release-dir", required=True)
    switch.add_argument("--current-link", required=True)
    switch.add_argument("--previous-link", required=True)

    rollback = sub.add_parser("rollback")
    rollback.add_argument("--current-link", required=True)
    rollback.add_argument("--previous-link", required=True)

    proof = sub.add_parser("write-proof")
    proof.add_argument("--proof-file", required=True)
    proof.add_argument("--current-link", required=True)
    proof.add_argument("--previous-link", required=True)
    proof.add_argument("--production-gate", default="PRODUCTION_GATE_OK")
    proof.add_argument("--health-url", required=True)
    proof.add_argument("--readiness-url", required=True)

    args = parser.parse_args()
    if args.command == "tree-digest":
        _print({"tree_sha256": release_tree_sha256(args.release_dir)})
    elif args.command == "validate":
        _print(validate_release(args.release_dir))
    elif args.command == "inspect":
        _print(resolve_link(args.link, required=args.required))
    elif args.command == "switch":
        _print(
            switch_release(
                release_dir=args.release_dir,
                current_link=args.current_link,
                previous_link=args.previous_link,
            )
        )
    elif args.command == "rollback":
        _print(
            rollback_release(
                current_link=args.current_link,
                previous_link=args.previous_link,
            )
        )
    elif args.command == "write-proof":
        _print(
            write_deployment_proof(
                proof_file=args.proof_file,
                current_link=args.current_link,
                previous_link=args.previous_link,
                production_gate=args.production_gate,
                health_url=args.health_url,
                readiness_url=args.readiness_url,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
