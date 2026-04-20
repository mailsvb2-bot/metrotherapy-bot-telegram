from __future__ import annotations
import logging


import re
import sys
from pathlib import Path


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "services"


REL_IMPORT_RX = re.compile(r"^\s*from\s+\.(\w+)\s+import\s+.+", re.MULTILINE)
REL_IMPORT_RX2 = re.compile(r"^\s*import\s+\.(\w+)\b", re.MULTILINE)

# Если кто-то сделает `from db import ...` внутри services — это тоже плохо.
BARE_IMPORTS = [
    "db",
    "schema",
    "store",
    "subscription",
    "events",
    "jobs",
    "pricing",
    "plan_store",
    "referrals",
    "gifts",
    "progress",
    "demo_analytics",
    "reminder",
    "funnel",
    "scheduler",
]
BARE_IMPORT_RX = re.compile(r"^\s*from\s+(" + "|".join(map(re.escape, BARE_IMPORTS)) + r")\s+import\s+.+", re.MULTILINE)


def main() -> int:
    if not SERVICES_DIR.exists():
        logger.info("ERROR: services/ not found")
        return 2

    bad = []

    for py in SERVICES_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")

        # относительные импорты внутри services запрещены
        if REL_IMPORT_RX.search(text) or REL_IMPORT_RX2.search(text):
            bad.append((py, "relative import (from .x import / import .x)"))
            continue

        # bare imports внутри services запрещены (должно быть from services.x import ...)
        if BARE_IMPORT_RX.search(text):
            bad.append((py, "bare import (from db/schema/... import ...)"))

    if not bad:
        logger.info("OK: imports are clean")
        return 0

    logger.info("FAIL: found import style violations in services/:")
    for py, why in bad:
        rel = py.relative_to(ROOT)
        logger.info(f"- {rel}: {why}")

    logger.info("\nFix rule:")
    logger.info("- Inside services/: use only absolute imports: `from services.xxx import ...`")
    logger.info("- Do NOT use relative imports like `from .xxx import ...`")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())