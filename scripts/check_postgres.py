from __future__ import annotations

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
