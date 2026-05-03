from __future__ import annotations

"""Offline readiness check for Telegram/MAX/VK channel configuration."""

import json
import sys

from interfaces.messaging.preflight import check_all_preflights


def main() -> int:
    statuses = check_all_preflights()
    payload = {
        "ok": all(status.ok for status in statuses),
        "channels": [
            {
                "channel": status.channel,
                "ok": status.ok,
                "missing": list(status.missing),
                "warnings": list(status.warnings),
                "details": status.details,
            }
            for status in statuses
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
