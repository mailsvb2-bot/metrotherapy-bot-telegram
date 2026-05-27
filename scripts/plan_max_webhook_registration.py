from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class MaxWebhookRegistrationPlan:
    ok: bool
    apply: bool
    public_base_url: str
    webhook_url: str
    token_configured: bool
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


def _mask_configured(value: str | None) -> bool:
    return bool((value or "").strip())


def build_plan(*, public_base_url: str, token: str | None, apply: bool) -> MaxWebhookRegistrationPlan:
    base = (public_base_url or "").strip().rstrip("/")
    warnings: list[str] = []
    errors: list[str] = []
    parsed = urlparse(base)
    if not base:
        errors.append("public_base_url is required")
    elif parsed.scheme != "https":
        errors.append("public_base_url must use https")
    elif not parsed.netloc:
        errors.append("public_base_url must include host")
    if not _mask_configured(token):
        errors.append("MAX_BOT_TOKEN is not configured")
    if not apply:
        warnings.append("dry_run_only: pass --apply to perform registration in a future implementation")
    return MaxWebhookRegistrationPlan(
        ok=not errors,
        apply=bool(apply),
        public_base_url=base,
        webhook_url=f"{base}/webhooks/max" if base else "",
        token_configured=_mask_configured(token),
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base-url", default=os.getenv("MESSENGER_PUBLIC_BASE_URL", ""))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    plan = build_plan(
        public_base_url=args.public_base_url,
        token=os.getenv("MAX_BOT_TOKEN"),
        apply=args.apply,
    )
    print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
    if args.apply:
        print("MAX webhook apply mode is intentionally not implemented yet; this tool is a safe planner.", file=sys.stderr)
        return 3
    return 0 if plan.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
