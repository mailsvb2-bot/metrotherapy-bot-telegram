from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.db.runtime import CONFIG, redacted_db_target
from services.db.core import get_connection


def main() -> int:
    print(f"engine={CONFIG.engine}")
    print(f"target={redacted_db_target()}")
    with get_connection() as conn:
        row = conn.execute("SELECT 1").fetchone()
    print(f"ping={row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
