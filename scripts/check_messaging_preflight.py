from __future__ import annotations

"""Offline readiness check for Telegram/MAX/VK channel configuration.

This CLI is diagnostic, not application boot. It must be able to run on a prod
server even when prod fail-fast variables are missing, so it temporarily forces
APP_ENV=dev before importing config.settings through preflight. Existing process
environment variables are still visible to the checks; this only prevents import
side effects from aborting before the report is printed.
"""

import json
import os
import sys


def main() -> int:
    os.environ["APP_ENV"] = "dev"

    from interfaces.messaging.preflight import check_all_preflights

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
