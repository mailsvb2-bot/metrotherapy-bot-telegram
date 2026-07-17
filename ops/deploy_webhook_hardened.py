from __future__ import annotations

"""Bounded, replay-safe HTTP front end for the canonical deploy webhook logic."""

from collections import OrderedDict
from http.server import ThreadingHTTPServer
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time

# The production installer copies this file to /root/deploy_webhook.py while the
# canonical implementation remains in /root/metrotherapy/ops.
_REPO_ROOT = Path(os.getenv("METROTHERAPY_APP_DIR", "/root/metrotherapy"))
try:
    _PRODUCTION_SOURCE_AVAILABLE = (_REPO_ROOT / "ops" / "deploy_webhook.py").is_file()
except OSError:
    _PRODUCTION_SOURCE_AVAILABLE = False
if not _PRODUCTION_SOURCE_AVAILABLE:
    _REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ops import deploy_webhook as legacy  # noqa: E402


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, default) or default).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    return min(max(value, minimum), maximum)


MAX_BODY_BYTES = _bounded_int(
    "GITHUB_WEBHOOK_MAX_BODY_BYTES",
    256 * 1024,
    minimum=1024,
    maximum=2 * 1024 * 1024,
)
READ_TIMEOUT_SEC = _bounded_int(
    "GITHUB_WEBHOOK_READ_TIMEOUT_SEC",
    5,
    minimum=1,
    maximum=30,
)
DELIVERY_CACHE_SIZE = _bounded_int(
    "GITHUB_WEBHOOK_DELIVERY_CACHE_SIZE",
    2048,
    minimum=64,
    maximum=100_000,
)
DELIVERY_TTL_SEC = _bounded_int(
    "GITHUB_WEBHOOK_DELIVERY_TTL_SEC",
    24 * 60 * 60,
    minimum=60,
    maximum=7 * 24 * 60 * 60,
)
_DELIVERY_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class _DeliveryReplayCache:
    """Process-local concurrent replay lock with bounded memory and TTL."""

    def __init__(self, *, max_items: int, ttl_sec: int) -> None:
        self._max_items = int(max_items)
        self._ttl_sec = int(ttl_sec)
        self._items: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def claim(self, delivery_id: str, *, now: float | None = None) -> bool:
        current = float(time.monotonic() if now is None else now)
        with self._lock:
            cutoff = current - self._ttl_sec
            while self._items:
                first_id, first_seen = next(iter(self._items.items()))
                if first_seen > cutoff:
                    break
                self._items.pop(first_id, None)
            if delivery_id in self._items:
                self._items.move_to_end(delivery_id)
                return False
            self._items[delivery_id] = current
            while len(self._items) > self._max_items:
                self._items.popitem(last=False)
            return True

    def release(self, delivery_id: str) -> None:
        with self._lock:
            self._items.pop(delivery_id, None)


_REPLAY_CACHE = _DeliveryReplayCache(
    max_items=DELIVERY_CACHE_SIZE,
    ttl_sec=DELIVERY_TTL_SEC,
)


def _parse_content_length(raw: str | None) -> tuple[int | None, int | None]:
    """Return ``(length, error_status)`` without allocating request data."""

    if raw is None or not str(raw).strip():
        return None, 411
    try:
        length = int(str(raw).strip(), 10)
    except (TypeError, ValueError):
        return None, 400
    if length <= 0:
        return None, 400
    if length > MAX_BODY_BYTES:
        return None, 413
    return length, None


def _valid_delivery_id(raw: str | None) -> str | None:
    delivery_id = str(raw or "").strip()
    return delivery_id if _DELIVERY_ID_RE.fullmatch(delivery_id) else None


def _trigger_already_observed(trigger_sha: str) -> bool:
    """Use bounded deploy log evidence to suppress replays across service restarts."""

    lines, _updated_at = legacy._read_log_tail(legacy.DEPLOY_LOG)  # noqa: SLF001
    for raw_line in lines:
        line = raw_line.strip()
        for pattern in (
            legacy._TRIGGER_LINE_RE,  # noqa: SLF001
            legacy._DEPLOY_STARTED_RE,  # noqa: SLF001
            legacy._DEPLOY_FINISHED_RE,  # noqa: SLF001
            legacy._RESULT_SKIP_RE,  # noqa: SLF001
            legacy._WORKER_COMPLETED_RE,  # noqa: SLF001
            legacy._TRIGGER_UNAVAILABLE_RE,  # noqa: SLF001
        ):
            match = pattern.match(line)
            if match and str(match.group(1)) == trigger_sha:
                return True
    return False


class Handler(legacy.Handler):
    def _read_bounded_body(self) -> bytes | None:
        length, error_status = _parse_content_length(self.headers.get("Content-Length"))
        if error_status is not None or length is None:
            messages = {400: b"bad content length", 411: b"content length required", 413: b"payload too large"}
            self._send(int(error_status or 400), messages.get(int(error_status or 400), b"bad request"))
            return None
        try:
            self.connection.settimeout(float(READ_TIMEOUT_SEC))
            body = self.rfile.read(length)
        except (TimeoutError, socket.timeout):
            self._send(408, b"request timeout")
            return None
        except OSError:
            self._send(400, b"request read failed")
            return None
        if len(body) != length:
            self._send(400, b"incomplete request body")
            return None
        return body

    def do_POST(self) -> None:
        if self.path != "/github-deploy":
            self._send(404, b"not found")
            return

        body = self._read_bounded_body()
        if body is None:
            return

        signature = self.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            legacy.SECRET.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        if not legacy.SECRET or not hmac.compare_digest(signature, expected):
            self._send(403, b"bad signature")
            return

        delivery_id = _valid_delivery_id(self.headers.get("X-GitHub-Delivery"))
        if delivery_id is None:
            self._send(400, b"bad delivery id")
            return
        if not _REPLAY_CACHE.claim(delivery_id):
            self._send(202, b"duplicate delivery")
            return

        event = self.headers.get("X-GitHub-Event", "")
        if event == "ping":
            self._send(200, b"pong")
            return
        if event != "push":
            self._send(202, b"ignored")
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, b"bad json")
            return
        if not isinstance(payload, dict):
            self._send(400, b"bad json object")
            return
        if payload.get("ref") != "refs/heads/main":
            self._send(202, b"not main")
            return

        trigger_sha = legacy._validated_trigger_sha(payload.get("after"))  # noqa: SLF001
        if trigger_sha is None:
            self._send(400, b"bad after sha")
            return
        if _trigger_already_observed(trigger_sha):
            self._send(202, b"trigger already observed")
            return

        try:
            legacy._run_deploy_background(trigger_sha)  # noqa: SLF001
        except legacy.DeployQueueError as exc:
            # Permit a genuine GitHub redelivery after a transient systemd failure.
            _REPLAY_CACHE.release(delivery_id)
            self._send(503, f"deploy queue failed: {exc}".encode("utf-8"))
            return
        self._send(202, b"deploy queued")


if __name__ == "__main__":
    ThreadingHTTPServer((legacy.HOST, legacy.PORT), Handler).serve_forever()
