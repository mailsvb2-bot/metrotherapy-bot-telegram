from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4

_T = TypeVar("_T")


def _retry_count() -> int:
    raw = (os.getenv("MESSENGER_PROVIDER_RETRIES") or "3").strip()
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 3


def _retry_backoff_sec(attempt: int) -> float:
    raw = (os.getenv("MESSENGER_PROVIDER_RETRY_BACKOFF_SEC") or "0.35").strip()
    try:
        base = max(0.05, float(raw))
    except (TypeError, ValueError):
        base = 0.35
    return min(base * (2 ** max(0, attempt - 1)), 3.0)


def _with_retries(operation: Callable[[], _T]) -> _T:
    last_exc: Exception | None = None
    for attempt in range(1, _retry_count() + 1):
        try:
            return operation()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt >= _retry_count():
                break
            time.sleep(_retry_backoff_sec(attempt))
    assert last_exc is not None
    raise last_exc


def json_request(
    url: str,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 20,
) -> dict[str, Any]:
    data = None
    req_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    def _request() -> dict[str, Any]:
        request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    return _with_retries(_request)


def form_request(url: str, params: dict[str, Any], *, timeout: float = 20) -> dict[str, Any]:
    encoded = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode("utf-8")

    def _request() -> dict[str, Any]:
        request = urllib.request.Request(url, data=encoded, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    return _with_retries(_request)


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
) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    content = path.read_bytes()
    body, boundary = multipart_bytes(field_name, path.name, content, content_type=mime_type)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    if token:
        headers["Authorization"] = token

    def _request() -> dict[str, Any]:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    return _with_retries(_request)
