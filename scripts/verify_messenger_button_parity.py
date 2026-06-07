from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from keyboards.inline import kb_demo_kind, kb_main, kb_mood_scale
from runtime import messenger_max_ui as max_ui
from runtime import messenger_vk_ui as vk_ui


def _flatten(rows