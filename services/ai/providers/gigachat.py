from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from services.ai.providers.base import AIProviderConfig

log = logging.getLogger(__name__)


class GigaChatProvider:
    """GigaChat adapter using stdlib HTTP only.

    Expected env is routed through AIProviderConfig:
    - credentials: GIGACHAT_CREDENTIALS value for Basic authorization
    - scope: GIGACHAT_SCOPE, usually GIGACHAT_API_PERS/B2B/CORP
    - base_url: chat API base, defaults in router
    """

    def __init__(self, config: AIProviderConfig) -> None:
        self.config = config
        self._access_token: str = ""

    def _token_url(self) -> str:
        return self.config.base_url.rstrip("/").replace("/api/v1", "/api/v2") + "/oauth"

    def _chat_url(self) -> str:
        return self.config.base_url.rstrip("/") + "/chat/completions"

    def _get_access_token(self) -> str | None:
        if self._access_token:
            return self._access_token
        if not self.config.credentials:
            return None
        body = urllib.parse.urlencode({"scope": self.config.scope or "GIGACHAT_API_PERS"}).encode("utf-8")
        req = urllib.request.Request(
            self._token_url(),
            data=body,
            method="POST",
            headers={
                "Authorization": f"Basic {self.config.credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=int(self.config.timeout_sec)) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data: dict[str, Any] = json.loads(raw)
            token = str(data.get("access_token") or "").strip()
            self._access_token = token
            return token or None
        except urllib.error.HTTPError as exc:
            try:
                exc.read()
            except OSError:
                pass
            log.warning("gigachat_oauth_http_error", extra={"status": getattr(exc, "code", None)})
            return None
        except TimeoutError:
            log.warning("gigachat_oauth_timeout")
            return None
        except (urllib.error.URLError, ConnectionError, OSError, json.JSONDecodeError, TypeError) as exc:
            log.warning("gigachat_oauth_failed", extra={"error_type": type(exc).__name__})
            return None

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 300) -> str | None:
        token = self._get_access_token()
        if not token:
            return None
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        req = urllib.request.Request(
            self._chat_url(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=int(self.config.timeout_sec)) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            try:
                exc.read()
            except OSError:
                pass
            log.warning("gigachat_http_error", extra={"status": getattr(exc, "code", None)})
            return None
        except TimeoutError:
            log.warning("gigachat_timeout")
            return None
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            log.warning("gigachat_transport_error", extra={"error_type": type(exc).__name__})
            return None

        try:
            obj: dict[str, Any] = json.loads(raw)
            choices = obj.get("choices") or []
            msg = (choices[0] or {}).get("message") or {}
            text = (msg.get("content") or "").strip()
            return text or None
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            log.warning("gigachat_bad_response", extra={"error_type": type(exc).__name__})
            return None
