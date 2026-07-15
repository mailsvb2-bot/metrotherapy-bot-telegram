from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# The deploy worker executes this file by absolute path. In that mode Python puts
# ``scripts/`` rather than the repository root on sys.path, so bootstrap the root
# before importing application services.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.practice_token_contract import telegram_stars_price  # noqa: E402


_AUDITED_PACKAGES = (
    "practice_start_7",
    "practice_60",
    "practice_antistress_60",
    "practice_personal_month",
)


def _api_call(token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed Telegram API host
            raw = response.read()
    except HTTPError as exc:
        raw = exc.read()
    except URLError:
        return {"ok": False, "error_code": 0, "description": "NETWORK_ERROR"}

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error_code": 0, "description": "INVALID_TELEGRAM_RESPONSE"}
    return decoded if isinstance(decoded, dict) else {
        "ok": False,
        "error_code": 0,
        "description": "INVALID_TELEGRAM_RESPONSE",
    }


def _safe_error(value: object) -> str:
    text = str(value or "").upper()
    for candidate in reversed(re.findall(r"[A-Z][A-Z0-9_]{2,}", text)):
        if "_" in candidate:
            return candidate[:80]
    return "TELEGRAM_API_ERROR"


def _bot_label(response: dict[str, Any]) -> str:
    result = response.get("result")
    if not isinstance(result, dict):
        return "unknown"
    username = str(result.get("username") or "").strip()
    return f"@{username}" if username else "unknown"


def _price_ladder_label() -> str:
    return ",".join(str(telegram_stars_price(package_id)) for package_id in _AUDITED_PACKAGES)


def run() -> tuple[str, int]:
    token = str(os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        return "status=error stage=config bot=unknown code=0 error=BOT_TOKEN_MISSING", 2

    try:
        prices = _price_ladder_label()
    except (TypeError, ValueError):
        return "status=error stage=prices bot=unknown code=0 error=INVALID_PRICE_LADDER", 6

    identity = _api_call(token, "getMe")
    if not identity.get("ok"):
        code = int(identity.get("error_code") or 0)
        error = _safe_error(identity.get("description"))
        return f"status=error stage=getMe bot=unknown code={code} error={error} prices={prices}", 3
    bot = _bot_label(identity)

    invoice = _api_call(
        token,
        "createInvoiceLink",
        {
            "title": "Metrotherapy Stars Audit",
            "description": "Production capability check for native Telegram Stars.",
            "payload": "metrotherapy-stars-provider-audit-v1",
            "currency": "XTR",
            "prices": [{"label": "Audit", "amount": 1}],
        },
    )
    if not invoice.get("ok"):
        code = int(invoice.get("error_code") or 0)
        error = _safe_error(invoice.get("description"))
        return f"status=error stage=createInvoiceLink bot={bot} code={code} error={error} prices={prices}", 4

    result = invoice.get("result")
    if not isinstance(result, str) or not result.startswith("https://"):
        return f"status=error stage=createInvoiceLink bot={bot} code=0 error=INVALID_INVOICE_LINK prices={prices}", 5
    return f"status=ok stage=createInvoiceLink bot={bot} code=200 error=NONE prices={prices}", 0


def main() -> int:
    message, code = run()
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
