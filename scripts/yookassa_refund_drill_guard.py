from __future__ import annotations

"""Secret-safe stage guard for the production YooKassa refund drill.

The underlying drill already converts expected operational failures to
RefundDrillError. This entrypoint additionally classifies unexpected exceptions
by stage and exception class without publishing exception messages, payloads,
credentials, or database contents.

Before attempting Git publication, the guard records the already-redacted result
in a root-owned deploy-state file. The observed deploy worker validates that
single-line record against the exact trigger and a strict character allowlist, so
it can publish the evidence even if the drill's own Git publisher fails.
"""

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import yookassa_refund_drill as impl  # noqa: E402

_DEFAULT_STATUS_FILE = Path(
    "/var/lib/metrotherapy/deploy-state/yookassa_refund_guard.status"
)


def _status_file() -> Path:
    raw = (os.getenv("YOOKASSA_GUARD_STATUS_FILE") or "").strip()
    return Path(raw) if raw else _DEFAULT_STATUS_FILE


def _record_safe_result(message: str) -> bool:
    safe = impl._safe_fragment(message, limit=900)
    if safe != message.strip():
        return False
    path = _status_file()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(safe + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _guard(stage: str, func: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except impl.RefundDrillError:
            raise
        except Exception as exc:  # validator: allow-wide-except
            exc_type = type(exc).__name__
            raise impl.RefundDrillError(f"unexpected_{stage}_{exc_type}") from exc

    return guarded


def _publish_with_fallback(message: str, *, success_code: int, fallback_code: int) -> int:
    _record_safe_result(message)
    try:
        impl._publish_result(message)
    except Exception:  # validator: allow-wide-except
        return fallback_code
    return success_code


def main() -> int:
    impl._require_trigger = _guard("trigger", impl._require_trigger)
    impl._prepare_environment = _guard("environment", impl._prepare_environment)
    impl._run_full_scenario = _guard("full", impl._run_full_scenario)
    impl._run_partial_scenario = _guard("partial", impl._run_partial_scenario)
    impl._run_reserved_scenario = _guard("reserved", impl._run_reserved_scenario)

    try:
        result = impl.run_drill()
    except impl.RefundDrillError as exc:
        reason = impl._safe_fragment(str(exc), limit=160)
        message = (
            f"{impl.RESULT_MARKER} trigger={impl.TRIGGER_SHA[:12] or 'NONE'} "
            f"status=blocked yookassa_refund=blocked reason={reason}"
        )
        return _publish_with_fallback(message, success_code=2, fallback_code=2)
    except Exception as exc:  # validator: allow-wide-except
        reason = f"unexpected_entry_{type(exc).__name__}"
        message = (
            f"{impl.RESULT_MARKER} trigger={impl.TRIGGER_SHA[:12] or 'NONE'} "
            f"status=blocked yookassa_refund=blocked reason={reason}"
        )
        return _publish_with_fallback(message, success_code=2, fallback_code=2)

    return _publish_with_fallback(result, success_code=0, fallback_code=3)


if __name__ == "__main__":
    raise SystemExit(main())
