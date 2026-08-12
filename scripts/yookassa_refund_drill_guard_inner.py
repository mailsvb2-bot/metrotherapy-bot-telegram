from __future__ import annotations

"""Secret-safe stage guard for the production YooKassa refund drill.

The production payment stack is imported lazily inside ``main`` so import-time
failures cannot bypass the redacted audit channel. Expected drill failures are
reduced to short reason codes; unexpected failures are reduced to stage + class
only. Exception text, provider payloads, credentials, and database contents are
never written to the audit status file.
"""

import importlib
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DEFAULT_STATUS_FILE = Path(
    "/var/lib/metrotherapy/deploy-state/yookassa_refund_guard.status"
)
_TRIGGER_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_RESULT_RE = re.compile(r"^[A-Za-z0-9_.:=,/\[\] -]+$")


def _status_file() -> Path:
    raw = (os.getenv("YOOKASSA_GUARD_STATUS_FILE") or "").strip()
    return Path(raw) if raw else _DEFAULT_STATUS_FILE


def _trigger_fragment() -> str:
    trigger = (os.getenv("DEPLOY_TRIGGER_SHA") or "").strip().lower()
    return trigger[:12] if _TRIGGER_RE.fullmatch(trigger) else "NONE"


def _record_safe_result(message: str) -> bool:
    safe = message.strip()
    if safe != message or len(safe) > 900 or _SAFE_RESULT_RE.fullmatch(safe) is None:
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


def _load_impl() -> Any:
    return importlib.import_module("scripts.yookassa_refund_drill")


def _guard(impl: Any, stage: str, func: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except impl.RefundDrillError:
            raise
        except SystemExit as exc:
            raise impl.RefundDrillError(f"unexpected_{stage}_SystemExit") from exc
        except Exception as exc:  # validator: allow-wide-except
            exc_type = type(exc).__name__
            raise impl.RefundDrillError(f"unexpected_{stage}_{exc_type}") from exc

    return guarded


def _publish_with_fallback(
    impl: Any,
    message: str,
    *,
    success_code: int,
    fallback_code: int,
) -> int:
    _record_safe_result(message)
    try:
        impl._publish_result(message)
    except SystemExit:
        return fallback_code
    except Exception:  # validator: allow-wide-except
        return fallback_code
    return success_code


def _record_import_failure(exc: BaseException) -> int:
    reason = f"unexpected_import_{type(exc).__name__}"
    message = (
        f"[ops-live-proof-result] trigger={_trigger_fragment()} "
        f"status=blocked yookassa_refund=blocked reason={reason}"
    )
    _record_safe_result(message)
    return 2


def main() -> int:
    try:
        impl = _load_impl()
    except (Exception, SystemExit) as exc:  # validator: allow-wide-except
        return _record_import_failure(exc)

    impl._require_trigger = _guard(impl, "trigger", impl._require_trigger)
    impl._prepare_environment = _guard(impl, "environment", impl._prepare_environment)
    impl._run_full_scenario = _guard(impl, "full", impl._run_full_scenario)
    impl._run_partial_scenario = _guard(impl, "partial", impl._run_partial_scenario)
    impl._run_reserved_scenario = _guard(impl, "reserved", impl._run_reserved_scenario)

    try:
        result = impl.run_drill()
    except impl.RefundDrillError as exc:
        reason = impl._safe_fragment(str(exc), limit=160)
        message = (
            f"{impl.RESULT_MARKER} trigger={impl.TRIGGER_SHA[:12] or 'NONE'} "
            f"status=blocked yookassa_refund=blocked reason={reason}"
        )
        return _publish_with_fallback(
            impl,
            message,
            success_code=2,
            fallback_code=2,
        )
    except SystemExit:
        message = (
            f"{impl.RESULT_MARKER} trigger={impl.TRIGGER_SHA[:12] or 'NONE'} "
            f"status=blocked yookassa_refund=blocked reason=unexpected_entry_SystemExit"
        )
        return _publish_with_fallback(
            impl,
            message,
            success_code=2,
            fallback_code=2,
        )
    except Exception as exc:  # validator: allow-wide-except
        reason = f"unexpected_entry_{type(exc).__name__}"
        message = (
            f"{impl.RESULT_MARKER} trigger={impl.TRIGGER_SHA[:12] or 'NONE'} "
            f"status=blocked yookassa_refund=blocked reason={reason}"
        )
        return _publish_with_fallback(
            impl,
            message,
            success_code=2,
            fallback_code=2,
        )

    return _publish_with_fallback(
        impl,
        result,
        success_code=0,
        fallback_code=3,
    )


if __name__ == "__main__":
    raise SystemExit(main())
