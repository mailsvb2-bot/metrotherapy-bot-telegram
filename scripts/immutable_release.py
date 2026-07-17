from __future__ import annotations

"""Atomic immutable-release switching for production deployment.

A release directory is complete only when its `.release.json` marker matches the
40-character Git commit used as the directory name and the release contains its
own Python interpreter and entrypoint. Runtime switching mutates only symlinks;
release contents are never changed during rollback.
"""

import argparse
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_MARKER = ".release.json"


@dataclass(frozen=True)
class ReleaseInfo:
    sha: str
    path: str
    python: str
    lock_file: str
    lock_sha256: str
    built_at_utc: str


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
    built_at = str(marker.get("built_at_utc") or "").strip()
    if not lock_file or not (release / lock_file).is_file():
        raise ValueError(f"release dependency lock is missing: {lock_file or '<empty>'}")
    if not re.fullmatch(r"[0-9a-f]{64}", lock_sha256):
        raise ValueError("release lock SHA-256 is invalid")
    if not built_at:
        raise ValueError("release build timestamp is missing")

    return ReleaseInfo(
        sha=sha,
        path=str(release),
        python=str(python_path),
        lock_file=lock_file,
        lock_sha256=lock_sha256,
        built_at_utc=built_at,
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
        "proof_version": 1,
        "proved_at_utc": _utc_now_iso(),
        "deployed_sha": current.sha,
        "release_dir": current.path,
        "release_python": current.python,
        "dependency_lock": current.lock_file,
        "dependency_lock_sha256": current.lock_sha256,
        "previous_sha": previous.sha if previous is not None else "",
        "previous_release_dir": previous.path if previous is not None else "",
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
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage immutable Metrotherapy runtime releases")
    sub = parser.add_subparsers(dest="command", required=True)

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
    if args.command == "validate":
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
