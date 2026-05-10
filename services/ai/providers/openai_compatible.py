from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from services.ai.providers.base import AIProviderConfig

log = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """Minimal stdlib Chat Completions provider.

    Works for OpenAI and providers exposing an OpenAI-compatible
    /chat/completions endpoint. No third-party SDK dependency.
    """

    def __init__(self, config: AIProviderConfig, *, extra_headers: dict[str, str] | None = None) -> None:
        self.config = config
        self._extra_headers = dict(extra_headers or {})

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        headers.update(self._extra_headers)
        return headers

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 300) -> str | None:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers=self._headers())

        try:
            with urllib.request.urlopen(req, timeout=int(self.config.timeout_sec)) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            try:
                exc.read()
            except OSError:
                pass
            log.warning("ai_provider_http_error", extra={"provider": self.config.name, "status": getattr(exc, "code", None)})
            return None
        except TimeoutError:
            log.warning("ai_provider_timeout", extra={"provider": self.config.name})
            return None
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            log.warning("ai_provider_transport_error", extra={"provider": self.config.name, "error_type": type(exc).__name__})
            return None

        try:
            obj: dict[str, Any] = json.loads(raw)
            choices = obj.get("choices") or []
            if not choices:
                log.warning("ai_provider_empty_choices", extra={"provider": self.config.name})
                return None
            msg = (choices[0] or {}).get("message") or {}
            text = (msg.get("content") or "").strip()
            return text or None
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            log.warning("ai_provider_bad_response", extra={"provider": self.config.name, "error_type": type(exc).__name__})
            return None
