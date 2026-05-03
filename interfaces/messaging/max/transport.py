from __future__ import annotations

"""MAX API transport boundary.

This client hides external MAX API details behind a small canonical boundary.
It is intentionally free of Metrotherapy business logic.
"""

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

MAX_PLATFORM_API_BASE_URL = "https://platform-api.max.ru"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class MaxTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class MaxTransportConfig:
    token: str
    api_base_url: str = MAX_PLATFORM_API_BASE_URL
    timeout_sec: float = 20.0
    max_attempts: int = 3

    def __post_init__(self) -> None:
        base = self.api_base_url.rstrip("/")
        object.__setattr__(self, "api_base_url", base)
        if not self.token.strip():
            raise ValueError("MAX token is empty")
        if "botapi.max.ru" in base:
            raise ValueError("MAX API legacy domain botapi.max.ru is forbidden")
        if not base.startswith(MAX_PLATFORM_API_BASE_URL):
            raise ValueError("MAX API base URL must start with https://platform-api.max.ru")


class MaxTransportClient:
    def __init__(self, config: MaxTransportConfig):
        self.config = config

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {"Authorization": self.config.token}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(self, method: str, path: str, *, payload: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith("/"):
            path = "/" + path
        url = self.config.api_base_url + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers=self._headers(json_body=payload is not None),
            method=method,
        )
        last_error: Exception | None = None
        for attempt in range(1, max(1, self.config.max_attempts) + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_sec) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS_CODES or attempt >= self.config.max_attempts:
                    detail = exc.read().decode("utf-8", "ignore")
                    raise MaxTransportError(f"MAX API {method} {path} failed: {exc.code} {detail}") from exc
                last_error = exc
            except OSError as exc:
                if attempt >= self.config.max_attempts:
                    raise MaxTransportError(f"MAX API {method} {path} network error: {exc}") from exc
                last_error = exc
            time.sleep(0.25 * attempt)
        raise MaxTransportError(str(last_error or "MAX API request failed"))

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/me")

    def list_subscriptions(self) -> dict[str, Any]:
        return self._request("GET", "/subscriptions")

    def create_subscription(self, *, url: str, secret: str, update_types: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/subscriptions",
            payload={"url": url, "secret": secret, "update_types": update_types},
        )

    def get_updates(self, *, limit: int | None = None, marker: str | None = None) -> dict[str, Any]:
        # Long Polling is a dev/test alternative to Webhook. Production should
        # not use it while a webhook subscription is active.
        return self._request("GET", "/updates", query={"limit": limit, "marker": marker})

    def send_message(self, *, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/messages", payload=payload, query={"user_id": user_id})
