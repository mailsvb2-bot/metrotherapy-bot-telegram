from __future__ import annotations

import argparse
import json

from services.accounts.merge import apply_account_merge, build_account_merge_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit or apply a controlled account merge.")
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--source", type=int, action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args()

    if args.apply:
        plan = apply_account_merge(args.target, args.source, reason=args.reason)
        payload = {"mode": "apply", "plan": plan.to_dict()}
    else:
        plan = build_account_merge_plan(args.target, args.source)
        payload = {"mode": "dry_run", "plan": plan.to_dict()}

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
