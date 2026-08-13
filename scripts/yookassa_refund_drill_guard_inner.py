from __future__ import annotations

"""Secret-safe stage guard for the production YooKassa refund drill.

The production payment stack is imported lazily inside ``main`` so import-time
failures cannot bypass the redacted audit channel. Expected drill failures are
reduced to short reason codes; unexpected failures are reduced to stage + class
only. Provider HTTP failures may expose only allowlisted provider ``code`` and
``parameter`` fields; descriptions, provider ids, payloads, credentials, and
database contents are never written to the audit status file.
"""

import hashlib
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
_PROVIDER_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_PROVIDER_PARAMETER_RE = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,160}$")
_PROVIDER_RESOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_YOOKASSA_IDEMPOTENCE_KEY_MAX = 64


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


def _normalize_idempotence_key(raw: object) -> str:
    """Return a YooKassa-safe stable key without weakening idempotency.

    The production refund drill historically used descriptive prefixes plus a
    UUID and could exceed YooKassa's 64-character header limit. Short keys are
    preserved exactly. Longer keys are deterministically reduced to a 64-char
    SHA-256 hex digest, so retries of the same logical operation still carry the
    same key while satisfying the provider contract.
    """

    value = str(raw or "").strip()
    if len(value) <= _YOOKASSA_IDEMPOTENCE_KEY_MAX:
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_provider_request_headers(impl: Any, request: Any) -> None:
    full_url = str(getattr(request, "full_url", request) or "")
    prefix = f"{str(impl._API_BASE).rstrip('/')}/"
    if not full_url.startswith(prefix) or not hasattr(request, "headers"):
        return
    headers = getattr(request, "headers", {})
    raw = headers.get("Idempotence-key") or headers.get("Idempotence-Key")
    if not raw:
        return
    normalized = _normalize_idempotence_key(raw)
    if normalized != raw:
        request.add_header("Idempotence-Key", normalized)


def _provider_http_error_reason(impl: Any, request: Any, exc: Any) -> str:
    """Reduce one provider HTTP error to non-sensitive contract coordinates.

    YooKassa's error body can contain an opaque request id and a human-readable
    description. Neither is needed for the drill and neither is allowed into the
    audit channel. Only syntactically allowlisted ``code`` and ``parameter`` are
    retained, plus the first API resource name and numeric HTTP status.
    """

    full_url = str(getattr(request, "full_url", request) or "")
    prefix = f"{str(impl._API_BASE).rstrip('/')}/"
    resource = "provider"
    if full_url.startswith(prefix):
        candidate = full_url[len(prefix):].split("/", 1)[0].strip()
        if _PROVIDER_RESOURCE_RE.fullmatch(candidate):
            resource = candidate

    provider_code = "unknown"
    parameter = "none"
    payload: Any = {}
    try:
        raw = exc.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        payload = impl.json.loads(str(raw or "{}"))
    except OSError:
        payload = {}
    except UnicodeError:
        payload = {}
    except ValueError:
        payload = {}
    except TypeError:
        payload = {}
    except AttributeError:
        payload = {}

    if isinstance(payload, dict):
        candidate_code = str(payload.get("code") or "").strip()
        if _PROVIDER_CODE_RE.fullmatch(candidate_code):
            provider_code = candidate_code
        candidate_parameter = str(payload.get("parameter") or "").strip()
        if _PROVIDER_PARAMETER_RE.fullmatch(candidate_parameter):
            parameter = candidate_parameter

    try:
        status = int(getattr(exc, "code", 0) or 0)
    except (TypeError, ValueError):
        status = 0
    return (
        f"provider_http_{status}:{resource}:"
        f"code={provider_code}:parameter={parameter}"
    )


def _install_provider_http_diagnostics(impl: Any) -> None:
    """Enforce provider headers and classify YooKassa API HTTP failures safely."""

    original_urlopen = impl.urllib.request.urlopen
    prefix = f"{str(impl._API_BASE).rstrip('/')}/"

    def diagnostic_urlopen(request: Any, *args: Any, **kwargs: Any) -> Any:
        _normalize_provider_request_headers(impl, request)
        try:
            return original_urlopen(request, *args, **kwargs)
        except impl.urllib.error.HTTPError as exc:
            full_url = str(getattr(request, "full_url", request) or "")
            if full_url.startswith(prefix):
                reason = _provider_http_error_reason(impl, request, exc)
                raise impl.RefundDrillError(reason) from exc
            raise

    impl.urllib.request.urlopen = diagnostic_urlopen


def _install_operation_guards(impl: Any) -> None:
    """Classify failures below scenario level without exposing provider details.

    The scenario guards tell us whether full/partial/reserved failed. These
    operation guards make a production blocker actionable while keeping the
    evidence allowlisted to operation + exception class only.
    """

    for name, stage in (
        ("_amount_value", "amount"),
        ("_create_test_payment", "payment_create"),
        ("_wait_payment_webhook", "payment_webhook"),
        ("_count", "db_count"),
        ("_create_refund", "refund_create"),
        ("_assert_full_refund", "full_refund_assert"),
        ("_reserve_one_probe_token", "reserve_token"),
        ("_wait_refund_state", "refund_state"),
    ):
        func = getattr(impl, name, None)
        if callable(func):
            setattr(impl, name, _guard(impl, stage, func))


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

    _install_provider_http_diagnostics(impl)
    _install_operation_guards(impl)
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