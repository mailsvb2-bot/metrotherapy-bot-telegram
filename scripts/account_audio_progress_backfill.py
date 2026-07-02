from __future__ import annotations

import argparse

from services.accounts.audio_backfill import (
    apply_account_audio_progress_backfill,
    build_account_audio_progress_backfill_plan,
    plan_to_json_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or apply account audio progress backfill from legacy user_audio_progress.")
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--source", type=int, action="append", required=True)
    parser.add_argument("--product-id", default="metrotherapy")
    parser.add_argument("--program-id", default="full_series")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.apply:
        plan = apply_account_audio_progress_backfill(
            args.target,
            args.source,
            product_id=args.product_id,
            program_id=args.program_id,
        )
        print(plan_to_json_payload("apply", plan))
        return 0

    plan = build_account_audio_progress_backfill_plan(
        args.target,
        args.source,
        product_id=args.product_id,
        program_id=args.program_id,
    )
    print(plan_to_json_payload("dry_run", plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
