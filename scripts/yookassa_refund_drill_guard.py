from __future__ import annotations

"""Secret-safe stage guard for the production YooKassa refund drill.

The underlying drill already converts expected operational failures to
RefundDrillError. This entrypoint additionally classifies unexpected exceptions
by stage and exception class without publishing exception messages, payloads,
credentials, or database contents.
"""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import yookassa_refund_drill as impl


def _guard(stage: str, func: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except impl.RefundDrillError:
            raise
        except Exception as exc:  # validator: allow-wide-except
            exc_type = type(exc).__name__
            raise impl.RefundDrillError(f"unexpected_{stage}_{exc_type}") from exc

    return guarded


def main() -> int:
    impl._require_trigger = _guard("trigger", impl._require_trigger)
    impl._prepare_environment = _guard("environment", impl._prepare_environment)
    impl._run_full_scenario = _guard("full", impl._run_full_scenario)
    impl._run_partial_scenario = _guard("partial", impl._run_partial_scenario)
    impl._run_reserved_scenario = _guard("reserved", impl._run_reserved_scenario)
    return impl.main()


if __name__ == "__main__":
    raise SystemExit(main())
