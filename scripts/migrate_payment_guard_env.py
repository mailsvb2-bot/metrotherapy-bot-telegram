#!/usr/bin/env python3
from __future__ import annotations

"""Atomically enforce mandatory production payment guard flags.

Only two non-secret boolean keys are managed. Existing secrets and unrelated
configuration are preserved byte-for-byte except for necessary line endings
around inserted managed keys. Duplicate managed keys fail closed.
"""

import argparse
import fcntl
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

MANAGED_VALUES = {
    "YOOKASSA_PROVIDER_VERIFICATION_REQUIRED": "1",
    "PAYMENT_CHECKOUT_INTENT_REQUIRED": "1",
}
_ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:export[ \t]+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*?)(?P<newline>\r?\n)?$"
)
_MARKER = "# Mandatory payment verification guards (managed atomically)"


class MigrationError(RuntimeError):
    """Raised when the authoritative env cannot be migrated safely."""


class MigrationResult(NamedTuple):
    changed: bool
    backup_path: Path | None


def _preferred_newline(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def _managed_assignments(lines: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    duplicates: set[str] = set()
    for index, line in enumerate(lines):
        match = _ASSIGNMENT_RE.match(line)
        if match is None:
            continue
        key = match.group("key")
        if key not in MANAGED_VALUES:
            continue
        if key in found:
            duplicates.add(key)
        else:
            found[key] = index
    if duplicates:
        raise MigrationError(
            "duplicate managed environment keys: " + ", ".join(sorted(duplicates))
        )
    return found


def _render(text: str) -> str:
    lines = text.splitlines(keepends=True)
    found = _managed_assignments(lines)
    newline = _preferred_newline(lines)

    for key, expected in MANAGED_VALUES.items():
        index = found.get(key)
        if index is None:
            continue
        match = _ASSIGNMENT_RE.match(lines[index])
        if match is None:  # pragma: no cover - guarded by discovery above
            raise MigrationError(f"managed key disappeared while rendering: {key}")
        prefix = match.group("prefix") or ""
        ending = match.group("newline") or newline
        lines[index] = f"{prefix}{key}={expected}{ending}"

    missing = [key for key in MANAGED_VALUES if key not in found]
    if missing:
        if lines and not lines[-1].endswith(("\n", "\r\n")):
            lines[-1] += newline
        if lines and lines[-1].strip():
            lines.append(newline)
        lines.append(f"{_MARKER}{newline}")
        for key in missing:
            lines.append(f"{key}={MANAGED_VALUES[key]}{newline}")
    return "".join(lines)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_replace(path: Path, data: bytes, *, mode: int, uid: int, gid: int) -> None:
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.payment-guard.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        os.fchmod(fd, mode)
        try:
            os.fchown(fd, uid, gid)
        except PermissionError:
            if os.geteuid() == 0:
                raise
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(fd)
        fd = -1
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        temp.unlink(missing_ok=True)


def _write_backup(path: Path, data: bytes, *, mode: int, uid: int, gid: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak.payment-guard.{stamp}.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(backup, flags, mode)
    try:
        os.fchmod(fd, mode)
        try:
            os.fchown(fd, uid, gid)
        except PermissionError:
            if os.geteuid() == 0:
                raise
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)
    return backup


def migrate_env_file(env_file: Path) -> MigrationResult:
    path = Path(env_file).expanduser()
    if not path.is_absolute():
        raise MigrationError("environment file path must be absolute")
    if path.is_symlink():
        raise MigrationError("environment file must not be a symbolic link")
    try:
        metadata = path.stat()
    except FileNotFoundError as exc:
        raise MigrationError("environment file does not exist") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise MigrationError("environment file must be a regular file")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & stat.S_IWOTH:
        raise MigrationError("environment file must not be world-writable")

    lock = path.with_name(f".{path.name}.payment-guard.lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock, flags, 0o600)
    except OSError as exc:
        raise MigrationError("cannot open migration lock file") from exc
    try:
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if path.is_symlink():
            raise MigrationError("environment file became a symbolic link")
        current = path.stat()
        if not stat.S_ISREG(current.st_mode):
            raise MigrationError("environment file is no longer regular")
        current_mode = stat.S_IMODE(current.st_mode)
        if current_mode & stat.S_IWOTH:
            raise MigrationError("environment file became world-writable")
        original = path.read_bytes()
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationError("environment file must be valid UTF-8") from exc
        updated = _render(text).encode("utf-8")
        if updated == original:
            return MigrationResult(changed=False, backup_path=None)

        backup = _write_backup(
            path,
            original,
            mode=current_mode,
            uid=current.st_uid,
            gid=current.st_gid,
        )
        try:
            _atomic_replace(
                path,
                updated,
                mode=current_mode,
                uid=current.st_uid,
                gid=current.st_gid,
            )
            verified = path.read_text(encoding="utf-8").splitlines(keepends=True)
            found = _managed_assignments(verified)
            for key, expected in MANAGED_VALUES.items():
                index = found.get(key)
                if index is None:
                    raise MigrationError(f"post-write verification failed for {key}")
                match = _ASSIGNMENT_RE.match(verified[index])
                actual = (match.group("value") if match is not None else "").strip()
                if actual != expected:
                    raise MigrationError(f"post-write verification failed for {key}")
        except (MigrationError, OSError, UnicodeError) as exc:
            try:
                _atomic_replace(
                    path,
                    original,
                    mode=current_mode,
                    uid=current.st_uid,
                    gid=current.st_gid,
                )
            except OSError as rollback_exc:
                raise MigrationError(
                    "payment guard migration failed and automatic rollback also failed"
                ) from rollback_exc
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError("post-write verification failed") from exc
        return MigrationResult(changed=True, backup_path=backup)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default="/etc/metrotherapy/metrotherapy.env",
        help="absolute path to the authoritative production environment file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = migrate_env_file(Path(args.env_file))
    except MigrationError as exc:
        print(f"PAYMENT_GUARD_ENV_MIGRATION_FAILED: {exc}", file=sys.stderr)
        return 2
    backup = str(result.backup_path) if result.backup_path is not None else "none"
    print(f"PAYMENT_GUARD_ENV_MIGRATION_OK changed={int(result.changed)} backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
