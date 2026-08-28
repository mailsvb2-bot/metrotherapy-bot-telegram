from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BUILD_RE = re.compile(r"^\.build-[0-9a-f]{40}\.[A-Za-z0-9_-]+$")


def _cache_root(cache_prefix: Path, releases_dir: Path) -> Path:
    if not cache_prefix.is_absolute() or not releases_dir.is_absolute():
        raise ValueError("cache prefix and releases dir must be absolute")
    return Path(str(cache_prefix).rstrip("/") + str(releases_dir))


def prune_release_python_cache(*, cache_prefix: Path, releases_dir: Path) -> int:
    cache_root = _cache_root(cache_prefix, releases_dir)
    if not cache_root.exists():
        return 0
    if cache_root.is_symlink() or not cache_root.is_dir():
        raise ValueError(f"unsafe cache root: {cache_root}")

    resolved_root = cache_root.resolve()
    live = {
        item.name
        for item in releases_dir.iterdir()
        if item.is_dir() and not item.is_symlink() and SHA_RE.fullmatch(item.name)
    }

    removed = 0
    for item in cache_root.iterdir():
        if item.is_symlink() or not item.is_dir():
            continue
        name = item.name
        stale_release = bool(SHA_RE.fullmatch(name)) and name not in live
        stale_build = bool(BUILD_RE.fullmatch(name))
        if not stale_release and not stale_build:
            continue
        resolved = item.resolve()
        if resolved.parent != resolved_root:
            raise ValueError(f"unsafe cache candidate: {resolved}")
        shutil.rmtree(resolved)
        removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune stale immutable-release Python cache directories")
    parser.add_argument("--cache-prefix", required=True)
    parser.add_argument("--releases-dir", required=True)
    args = parser.parse_args()
    removed = prune_release_python_cache(
        cache_prefix=Path(args.cache_prefix),
        releases_dir=Path(args.releases_dir),
    )
    print(f"IMMUTABLE_RELEASE_CACHE_CLEANUP_OK removed={removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
