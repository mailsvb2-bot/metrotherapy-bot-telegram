from __future__ import annotations

import argparse

from services.accounts.premium_backfill import (
    apply_account_premium_backfill,
    build_account_premium_backfill_plan,
    plan_to_json_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or apply account-native premium row backfill.")
    parser.add_argument("--target", type=int, required=True, help="Canonical account id to receive legacy premium rows.")
    parser.add_argument("--source", type=int, action="append", default=None, help="Optional source user id. May be repeated.")
    parser.add_argument("--apply", action="store_true", help="Apply the planned backfill. Without this flag the command is read-only.")
    args = parser.parse_args()

    if args.apply:
        plan = apply_account_premium_backfill(args.target, args.source)
        print(plan_to_json_payload("apply", plan))
        return 0

    plan = build_account_premium_backfill_plan(args.target, args.source)
    print(plan_to_json_payload("dry_run", plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
