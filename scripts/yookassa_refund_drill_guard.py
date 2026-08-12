from __future__ import annotations

"""Stdlib-only process guard around the YooKassa refund audit.

This outer layer intentionally imports no application/payment modules. It wraps
execution of the inner refund guard so an import-time or other Python-level
failure cannot bypass the secret-safe deploy-state evidence channel.
"""

import os
import re
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_INNER_GUARD = ROOT / "scripts" / "yookassa_refund_drill_guard_inner.py"
_DEFAULT_STATUS_FILE = Path(
    "/var/lib/metrotherapy/deploy-state/yookassa_refund_guard.status"
)
_TRIGGER_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_RESULT_RE = re.compile(r"^[A-Za-z0-9_.:=,/\[\] -]+$")


def _inner_guard_path() -> Path:
    raw = (os.getenv("YOOKASSA_REFUND_INNER_GUARD") or "").strip()
    return Path(raw) if raw else _DEFAULT_INNER_GUARD


def _status_file() -> Path:
    raw = (os.getenv("YOOKASSA_GUARD_STATUS_FILE") or "").strip()
    return Path(raw) if raw else _DEFAULT_STATUS_FILE


def _trigger_fragment() -> str:
    trigger = (os.getenv("DEPLOY_TRIGGER_SHA") or "").strip().lower()
    return trigger[:12] if _TRIGGER_RE.fullmatch(trigger) else "NONE"


def _is_safe_result(line: str) -> bool:
    if not line or len(line) > 900 or _SAFE_RESULT_RE.fullmatch(line) is None:
        return False
    trigger = _trigger_fragment()
    return line.startswith(
        f"[ops-live-proof-result] trigger={trigger} status=blocked "
    ) or line.startswith(f"[ops-live-proof-result] trigger={trigger} status=ok ")


def _cached_result_exists() -> bool:
    path = _status_file()
    try:
        line = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeError):
        return False
    return _is_safe_result(line)


def _record_failure(reason: str) -> bool:
    reason_safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(reason))[:120] or "unknown"
    message = (
        f"[ops-live-proof-result] trigger={_trigger_fragment()} status=blocked "
        f"yookassa_refund=blocked reason={reason_safe}"
    )
    if not _is_safe_result(message):
        return False
    path = _status_file()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(message + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def main() -> int:
    try:
        runpy.run_path(str(_inner_guard_path()), run_name="__main__")
    except KeyboardInterrupt:
        raise
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code == 0:
            return 0
        if _cached_result_exists():
            return code if 0 < code < 126 else 2
        _record_failure("unexpected_guard_exit_SystemExit")
        return 2
    except BaseException as exc:  # validator: allow-wide-except
        _record_failure(f"unexpected_bootstrap_{type(exc).__name__}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
