from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any


API_BASE = "https://platform-api2.max.ru"


def _context() -> ssl.SSLContext:
    bundle = (os.getenv("MAX_CA_BUNDLE") or "").strip()
    return ssl.create_default_context(cafile=bundle or None)


def _api_call(token: str, path: str) -> tuple[dict[str, Any], int]:
    request = urllib.request.Request(
        API_BASE + path,
        headers={"Authorization": token},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20, context=_context()) as response:
            raw = response.read().decode("utf-8")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = int(exc.code)
    except (urllib.error.URLError, OSError, ssl.SSLError):
        return {"error": "NETWORK_OR_TLS_ERROR"}, 0
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"error": "INVALID_JSON"}
    if isinstance(payload, dict):
        return payload, status
    if isinstance(payload, list):
        return {"subscriptions": payload}, status
    return {"error": "INVALID_RESPONSE"}, status


def _subscriptions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("subscriptions", payload.get("items", payload.get("data", [])))
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _safe_error(payload: dict[str, Any], fallback: str) -> str:
    raw = payload.get("error") or payload.get("message") or payload.get("code") or fallback
    if isinstance(raw, dict):
        raw = raw.get("code") or raw.get("message") or fallback
    return str(raw).strip().upper().replace(" ", "_")[:80] or fallback


def run() -> tuple[str, int]:
    token = (os.getenv("MAX_BOT_TOKEN") or "").strip()
    public_base = (os.getenv("MESSENGER_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    configured_api = (os.getenv("MAX_API_BASE_URL") or API_BASE).strip().rstrip("/")
    if not token:
        return "status=error stage=config bot=unknown code=0 error=MAX_BOT_TOKEN_MISSING", 2
    if not public_base.startswith("https://"):
        return "status=error stage=config bot=unknown code=0 error=PUBLIC_BASE_INVALID", 3
    if configured_api != API_BASE:
        return "status=error stage=config bot=unknown code=0 error=MAX_API_BASE_NOT_API2", 4

    me, me_status = _api_call(token, "/me")
    if me_status != 200:
        return f"status=error stage=me bot=unknown code={me_status} error={_safe_error(me, 'MAX_API_ERROR')}", 5
    username = str(me.get("username") or me.get("name") or "unknown").strip().replace(" ", "_")[:80]

    subscriptions, sub_status = _api_call(token, "/subscriptions")
    if sub_status != 200:
        return (
            f"status=error stage=subscriptions bot={username} code={sub_status} "
            f"error={_safe_error(subscriptions, 'MAX_API_ERROR')}"
        ), 6

    expected = public_base + "/webhooks/max"
    active = _subscriptions(subscriptions)
    present = any(str(item.get("url") or "").rstrip("/") == expected for item in active)
    if not present:
        return (
            f"status=error stage=subscriptions bot={username} code=200 "
            "error=WEBHOOK_NOT_REGISTERED api=platform-api2.max.ru"
        ), 7
    return (
        f"status=ok stage=subscriptions bot={username} code=200 "
        "error=NONE api=platform-api2.max.ru webhook=present"
    ), 0


def main() -> int:
    message, code = run()
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
