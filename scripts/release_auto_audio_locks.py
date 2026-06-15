from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = Path("/etc/metrotherapy/metrotherapy.env")


def _load_env_file(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    env_path = Path(path)
    if not env_path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        try:
            parts = shlex.split(value, posix=True)
            loaded[key] = parts[0] if len(parts) == 1 else value
        except ValueError:
            loaded[key] = value.strip('"').strip("'")
    return loaded


def _apply_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ.setdefault(str(key), str(value))


def main() -> int:
    parser = argparse.ArgumentParser(description="List or release stale auto-audio delivery locks")
    parser.add_argument("--env-file", default=os.getenv("METROTHERAPY_ENV_FILE", str(DEFAULT_ENV_FILE)))
    parser.add_argument("--apply", action="store_true", help="Actually delete stale auto-audio lock rows")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--stale-after-seconds", type=int, default=None)
    args = parser.parse_args()

    _apply_env(_load_env_file(args.env_file))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from services.auto_audio_recovery import list_stale_auto_audio_locks, release_stale_auto_audio_locks
    from services.storage_legacy_audit import storage_legacy_audit

    audit = storage_legacy_audit()
    if audit.active_engine != "postgres" or audit.hard_failures:
        payload = {
            "ok": False,
            "action": "refuse",
            "reason": "storage_audit_not_green_enough",
            "active_engine": audit.active_engine,
            "storage_status": audit.status,
            "hard_failures": audit.hard_failures,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 1

    locks = list_stale_auto_audio_locks(stale_after_seconds=args.stale_after_seconds, limit=args.limit)
    released = 0
    if args.apply:
        released = release_stale_auto_audio_locks(
            stale_after_seconds=args.stale_after_seconds,
            limit=args.limit,
            dry_run=False,
        )
    payload = {
        "ok": True,
        "action": "released" if args.apply else "dry_run",
        "dry_run": not bool(args.apply),
        "candidate_count": len(locks),
        "released_count": int(released),
        "locks": [lock.__dict__ for lock in locks],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    print(f"AUTO_AUDIO_LOCK_RELEASE action={payload['action']} candidates={len(locks)} released={released}")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
