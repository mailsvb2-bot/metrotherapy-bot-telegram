from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.storage_legacy_audit import format_storage_legacy_audit_for_admin, storage_legacy_audit  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit active DB storage and legacy SQLite ambiguity")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when the audit has hard findings")
    args = parser.parse_args()

    audit = storage_legacy_audit()
    if args.json:
        print(json.dumps(audit.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(format_storage_legacy_audit_for_admin())

    if args.strict and audit.hard_failures:
        print("STORAGE_LEGACY_AUDIT_NOT_OK hard_findings=" + ",".join(audit.hard_failures), file=sys.stderr)
        return 1
    if not args.json:
        print("STORAGE_LEGACY_AUDIT_OK status=" + audit.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
