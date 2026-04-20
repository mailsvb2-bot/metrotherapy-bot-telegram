from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.messenger.setup import validate_setup


if __name__ == "__main__":
    strict = "--strict" in sys.argv
    ok, text = validate_setup(strict=strict)
    print(text)
    raise SystemExit(0 if ok else 1)
