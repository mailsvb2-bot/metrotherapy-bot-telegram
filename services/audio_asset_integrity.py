from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ASSET_MANIFEST = ".asset-manifest.json"
_RELEASE_POINTER = ".audio-assets.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AudioAssetInfo:
    asset_dir: str
    asset_sha256: str
    file_count: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


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


def asset_tree_sha256(path: str | Path) -> tuple[str, int]:
    """Hash a normalized audio tree without following links or special files."""

    root = Path(path).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"audio asset path is not a directory: {root}")

    digest = hashlib.sha256()
    metadata = root.lstat()
    _digest_entry(
        digest,
        relative=".",
        kind=b"D",
        mode=stat.S_IMODE(metadata.st_mode),
        payload=b"",
    )
    file_count = 0
    entries = sorted(
        (
            item
            for item in root.rglob("*")
            if item.relative_to(root).as_posix() != _ASSET_MANIFEST
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for item in entries:
        relative = item.relative_to(root).as_posix()
        item_metadata = item.lstat()
        mode = stat.S_IMODE(item_metadata.st_mode)
        if item.is_symlink():
            raise ValueError(f"audio assets must not contain symlinks: {relative}")
        if item.is_dir():
            kind = b"D"
            payload = b""
        elif item.is_file():
            kind = b"F"
            file_count += 1
            hasher = hashlib.sha256()
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            payload = hasher.digest()
        else:
            raise ValueError(f"audio assets contain unsupported entry: {relative}")
        _digest_entry(
            digest,
            relative=relative,
            kind=kind,
            mode=mode,
            payload=payload,
        )
    if file_count <= 0:
        raise ValueError(f"audio asset directory is empty: {root}")
    return digest.hexdigest(), file_count


def seal_asset_dir(path: str | Path) -> AudioAssetInfo:
    root = Path(path).resolve(strict=True)
    asset_sha256, file_count = asset_tree_sha256(root)
    _atomic_json(
        root / _ASSET_MANIFEST,
        {
            "schema_version": 1,
            "asset_sha256": asset_sha256,
            "file_count": file_count,
            "sealed_at_utc": _utc_now_iso(),
        },
        mode=0o440,
    )
    return AudioAssetInfo(str(root), asset_sha256, file_count)


def validate_asset_dir(
    path: str | Path,
    *,
    expected_sha256: str = "",
    expected_file_count: int | None = None,
) -> AudioAssetInfo:
    root = Path(path).resolve(strict=True)
    marker = _load_json(root / _ASSET_MANIFEST, label="audio asset manifest")
    marker_sha = str(marker.get("asset_sha256") or "").strip()
    try:
        marker_count = int(marker.get("file_count"))
    except (TypeError, ValueError) as exc:
        raise ValueError("audio asset manifest file_count is invalid") from exc
    if not _SHA256_RE.fullmatch(marker_sha):
        raise ValueError("audio asset manifest SHA-256 is invalid")
    if root.name != marker_sha:
        raise ValueError("audio asset directory name does not match its content digest")
    actual_sha, actual_count = asset_tree_sha256(root)
    if actual_sha != marker_sha:
        raise ValueError("audio asset content changed after immutable publication")
    if actual_count != marker_count:
        raise ValueError("audio asset file count changed after immutable publication")
    if expected_sha256 and marker_sha != str(expected_sha256).strip():
        raise ValueError("release audio asset SHA-256 does not match the asset manifest")
    if expected_file_count is not None and marker_count != int(expected_file_count):
        raise ValueError("release audio asset file count does not match the asset manifest")
    return AudioAssetInfo(str(root), marker_sha, marker_count)


def write_release_pointer(release_dir: str | Path, asset_dir: str | Path) -> AudioAssetInfo:
    release = Path(release_dir).resolve(strict=True)
    info = validate_asset_dir(asset_dir)
    _atomic_json(
        release / _RELEASE_POINTER,
        {
            "schema_version": 1,
            "asset_dir": info.asset_dir,
            "asset_sha256": info.asset_sha256,
            "file_count": info.file_count,
        },
        mode=0o444,
    )
    return info


def validate_release_assets(
    release_dir: str | Path,
    *,
    require_versioned: bool = False,
) -> AudioAssetInfo | None:
    release = Path(release_dir).resolve(strict=True)
    pointer_path = release / _RELEASE_POINTER
    if not pointer_path.is_file():
        if require_versioned:
            raise ValueError(f"versioned audio asset pointer is missing: {pointer_path}")
        return None
    pointer = _load_json(pointer_path, label="release audio asset pointer")
    asset_dir_raw = str(pointer.get("asset_dir") or "").strip()
    expected_sha = str(pointer.get("asset_sha256") or "").strip()
    try:
        expected_count = int(pointer.get("file_count"))
    except (TypeError, ValueError) as exc:
        raise ValueError("release audio asset file_count is invalid") from exc
    if not asset_dir_raw or not Path(asset_dir_raw).is_absolute():
        raise ValueError("release audio asset directory must be an absolute path")
    audio_link = release / "audio"
    if not audio_link.is_symlink():
        raise ValueError(f"release audio entry must be a symlink: {audio_link}")
    try:
        linked_dir = audio_link.resolve(strict=True)
        asset_dir = Path(asset_dir_raw).resolve(strict=True)
    except OSError as exc:
        raise ValueError("release audio asset link is dangling") from exc
    if linked_dir != asset_dir:
        raise ValueError("release audio symlink does not match the sealed asset pointer")
    return validate_asset_dir(
        asset_dir,
        expected_sha256=expected_sha,
        expected_file_count=expected_count,
    )


def augment_deployment_proof(
    proof_file: str | Path,
    *,
    current_release: str | Path,
    previous_release: str | Path | None = None,
) -> dict[str, Any]:
    destination = Path(proof_file)
    payload = _load_json(destination, label="deployment proof")
    current = validate_release_assets(current_release, require_versioned=True)
    assert current is not None
    payload.update(
        {
            "audio_asset_dir": current.asset_dir,
            "audio_asset_sha256": current.asset_sha256,
            "audio_asset_file_count": current.file_count,
        }
    )
    previous = (
        validate_release_assets(previous_release, require_versioned=False)
        if previous_release is not None
        else None
    )
    payload.update(
        {
            "previous_audio_asset_dir": previous.asset_dir if previous else "",
            "previous_audio_asset_sha256": previous.asset_sha256 if previous else "",
            "previous_audio_asset_file_count": previous.file_count if previous else 0,
        }
    )
    _atomic_json(destination, payload, mode=0o644)
    return payload


def referenced_asset_dirs(releases_dir: str | Path) -> tuple[str, ...]:
    root = Path(releases_dir)
    if not root.is_dir():
        return ()
    referenced: set[str] = set()
    for pointer_path in root.glob(f"*/{_RELEASE_POINTER}"):
        try:
            pointer = _load_json(pointer_path, label="release audio asset pointer")
            raw = str(pointer.get("asset_dir") or "").strip()
            if raw:
                referenced.add(str(Path(raw).resolve(strict=False)))
        except ValueError:
            continue
    return tuple(sorted(referenced))


def _print(value: Any) -> None:
    if isinstance(value, AudioAssetInfo):
        value = asdict(value)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Seal and validate Metrotherapy audio assets")
    sub = parser.add_subparsers(dest="command", required=True)

    seal = sub.add_parser("seal")
    seal.add_argument("asset_dir")

    validate_dir = sub.add_parser("validate-dir")
    validate_dir.add_argument("asset_dir")
    validate_dir.add_argument("--expected-sha256", default="")

    pointer = sub.add_parser("write-release-pointer")
    pointer.add_argument("--release-dir", required=True)
    pointer.add_argument("--asset-dir", required=True)

    validate_release = sub.add_parser("validate-release")
    validate_release.add_argument("release_dir")
    validate_release.add_argument("--require-versioned", action="store_true")

    proof = sub.add_parser("augment-proof")
    proof.add_argument("--proof-file", required=True)
    proof.add_argument("--current-release", required=True)
    proof.add_argument("--previous-release", default="")

    referenced = sub.add_parser("referenced-assets")
    referenced.add_argument("releases_dir")

    args = parser.parse_args()
    if args.command == "seal":
        _print(seal_asset_dir(args.asset_dir))
    elif args.command == "validate-dir":
        _print(validate_asset_dir(args.asset_dir, expected_sha256=args.expected_sha256))
    elif args.command == "write-release-pointer":
        _print(write_release_pointer(args.release_dir, args.asset_dir))
    elif args.command == "validate-release":
        _print(
            validate_release_assets(
                args.release_dir,
                require_versioned=bool(args.require_versioned),
            )
        )
    elif args.command == "augment-proof":
        previous = args.previous_release or None
        _print(
            augment_deployment_proof(
                args.proof_file,
                current_release=args.current_release,
                previous_release=previous,
            )
        )
    elif args.command == "referenced-assets":
        _print({"asset_dirs": referenced_asset_dirs(args.releases_dir)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
