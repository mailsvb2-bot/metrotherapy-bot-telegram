from __future__ import annotations

import argparse

from services.accounts.diagnostics import build_account_diagnostics, diagnostics_to_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Print account identity, audio, and practice wallet diagnostics.")
    parser.add_argument("--account", type=int, required=True)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when diagnostics contain warnings.")
    args = parser.parse_args()

    payload = build_account_diagnostics(args.account)
    print(diagnostics_to_json(payload))

    if args.strict and payload.get("warnings"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
