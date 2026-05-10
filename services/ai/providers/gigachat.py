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
        except urllib.error.URLError as exc:
            log.warning("gigachat_oauth_failed", extra={"error_type": type(exc).__name__})
            return None
        except ConnectionError as exc:
            log.warning("gigachat_oauth_failed", extra={"error_type": type(exc).__name__})
            return None
        except OSError as exc:
            log.warning("gigachat_oauth_failed", extra={"error_type": type(exc).__name__})
            return None

        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("gigachat_oauth_failed", extra={"error_type": type(exc).__name__})
            return None
        token = str(data.get("access_token") or "").strip()
        self._access_token = token
        return token or None

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
        except urllib.error.URLError as exc:
            log.warning("gigachat_transport_error", extra={"error_type": type(exc).__name__})
            return None
        except ConnectionError as exc:
            log.warning("gigachat_transport_error", extra={"error_type": type(exc).__name__})
            return None
        except OSError as exc:
            log.warning("gigachat_transport_error", extra={"error_type": type(exc).__name__})
            return None

        try:
            obj: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("gigachat_bad_response", extra={"error_type": type(exc).__name__})
            return None

        choices = obj.get("choices") or []
        if not isinstance(choices, list) or not choices:
            log.warning("gigachat_bad_response", extra={"error_type": "empty_choices"})
            return None
        first = choices[0]
        if not isinstance(first, dict):
            log.warning("gigachat_bad_response", extra={"error_type": "choice_not_object"})
            return None
        msg = first.get("message") or {}
        if not isinstance(msg, dict):
            log.warning("gigachat_bad_response", extra={"error_type": "message_not_object"})
            return None
        text = (msg.get("content") or "").strip()
        return text or None
