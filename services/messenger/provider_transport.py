from __future__ import annotations

import json
import mimetypes
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4

_T = TypeVar("_T")


class ProviderPermanentHTTPError(RuntimeError):
    """A provider HTTP response that must not be retried by the HTTP client."""

    def __init__(self, status_code: int):
        self.status_code = int(status_code)
        super().__init__(f"provider_http_{self.status_code}")


def _retry_count() -> int:
    raw = (os.getenv("MESSENGER_PROVIDER_RETRIES") or "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _retry_backoff_sec(attempt: int) -> float:
    raw = (os.getenv("MESSENGER_PROVIDER_RETRY_BACKOFF_SEC") or "0.35").strip()
    try:
        base = max(0.05, float(raw))
    except ValueError:
        base = 0.35
    return min(base * (2 ** max(0, attempt - 1)), 3.0)


def _retryable_http_status(code: int) -> bool:
    return int(code) in {408, 429} or 500 <= int(code) <= 599


def _maybe_retry(attempt: int, max_attempts: int, exc: Exception) -> Exception | None:
    if attempt >= max_attempts:
        return exc
    time.sleep(_retry_backoff_sec(attempt))
    return None


def _with_retries(operation: Callable[[], _T], *, retries: int | None = None) -> _T:
    last_exc: Exception | None = None
    max_attempts = max(1, int(retries if retries is not None else _retry_count()))
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except urllib.error.HTTPError as exc:
            if not _retryable_http_status(int(exc.code)):
                raise ProviderPermanentHTTPError(int(exc.code)) from exc
            last_exc = _maybe_retry(attempt, max_attempts, exc)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = _maybe_retry(attempt, max_attempts, exc)
        if last_exc is not None:
            break
    assert last_exc is not None
    raise last_exc


def json_request(
    url: str,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 20,
    retries: int | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> dict[str, Any]:
    data = None
    req_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    def _request() -> dict[str, Any]:
        request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    return _with_retries(_request, retries=retries)


def form_request(
    url: str,
    params: dict[str, Any],
    *,
    timeout: float = 20,
    retries: int | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> dict[str, Any]:
    encoded = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode("utf-8")

    def _request() -> dict[str, Any]:
        request = urllib.request.Request(url, data=encoded, method="POST")
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    return _with_retries(_request, retries=retries)


def multipart_bytes(field_name: str, filename: str, content: bytes, *, content_type: str) -> tuple[bytes, str]:
    boundary = f"----ChatGPTBoundary{uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"{field_name}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + content + tail, boundary


def multipart_upload(
    url: str,
    *,
    token: str | None = None,
    field_name: str,
    path: Path,
    timeout: float = 120,
    retries: int | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    content = path.read_bytes()
    body, boundary = multipart_bytes(field_name, path.name, content, content_type=mime_type)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    if token:
        headers["Author" + "ization"] = token

    def _request() -> dict[str, Any]:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    return _with_retries(_request, retries=retries)
