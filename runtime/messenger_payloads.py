from __future__ import annotations

import hashlib
import json
from typing import Any

from services.messenger.menu_contract import normalize_menu_command


def _format_score_for_text_ui(score: int) -> str:
    if int(score) > 0:
        return f"+{int(score)}"
    return str(int(score))


def _score_command_value(value: str) -> str | None:
    raw = str(value or "").strip().casefold().replace("−", "-")
    if raw.startswith("score:"):
        candidate = raw