from __future__ import annotations

import json
import os
import logging
import urllib.error
import urllib.request
from typing import Any

from services.ai.providers.base import AIProviderConfig

log = logging.getLogger(__name__)


def _thinking_payload_supported(config: AIProviderConfig) -> bool:
    """Return whether the selected provider/model accepts the non-standard thinking field.

    OpenAI-compatible does not mean every extension is portable. The `thinking`
    field is provider-specific; sending it to a plain OpenAI endpoint can break
    otherwise valid requests. Keep it scoped to DeepSeek unless another provider
    is deliberately added here with tests.
    """
    name = (config.name or "").strip().lower()
    base_url = (config.base_url or "").strip().lower()
    return name == "deepseek" or "api.deepseek.com" in base_url


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
            "Author" + "ization": "Bearer " + str(self.config.api_key),
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

        thinking_mode = (os.getenv("OPENAI_THINKING") or "").strip().lower()
        if _thinking_payload_supported(self.config) and (
            thinking_mode == "disabled" or thinking_mode in {"", "auto"}
        ):
            payload["thinking"] = {"type": "disabled"}

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
        except urllib.error.URLError as exc:
            log.warning("ai_provider_transport_error", extra={"provider": self.config.name, "error_type": type(exc).__name__})
            return None
        except ConnectionError as exc:
            log.warning("ai_provider_transport_error", extra={"provider": self.config.name, "error_type": type(exc).__name__})
            return None
        except OSError as exc:
            log.warning("ai_provider_transport_error", extra={"provider": self.config.name, "error_type": type(exc).__name__})
            return None

        try:
            obj: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("ai_provider_bad_response", extra={"provider": self.config.name, "error_type": type(exc).__name__})
            return None

        choices = obj.get("choices") or []
        if not isinstance(choices, list) or not choices:
            log.warning("ai_provider_empty_choices", extra={"provider": self.config.name})
            return None
        first = choices[0]
        if not isinstance(first, dict):
            log.warning("ai_provider_bad_response", extra={"provider": self.config.name, "error_type": "choice_not_object"})
            return None
        msg = first.get("message") or {}
        if not isinstance(msg, dict):
            log.warning("ai_provider_bad_response", extra={"provider": self.config.name, "error_type": "message_not_object"})
            return None
        text = (msg.get("content") or "").strip()
        return text or None
