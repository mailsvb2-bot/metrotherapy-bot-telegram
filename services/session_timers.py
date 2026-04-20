from __future__ import annotations
"""@deprecated

Legacy scheduler (scheduled_jobs / unix time) is intentionally removed from runtime.

✅ Single scheduler is now: services.jobs + core.engine.Engine.tick only.

This module is kept only for reference / emergency rollback, but must NOT be imported
or executed. Any attempt to use it is logged as ERROR and fails fast.
"""


import logging
from typing import Any, NoReturn

logger = logging.getLogger(__name__)


def _deprecated(*_a: Any, **_k: Any) -> NoReturn:
    logger.error(
        "session_timers is deprecated. Use services.jobs + engine.tick only"
    )
    raise RuntimeError(
        "session_timers is deprecated. Use services.jobs + engine.tick only"
    )


# Public API kept for compatibility with old code paths (should never be called).
add_job = _deprecated
cancel_job = _deprecated
tick_jobs = _deprecated
