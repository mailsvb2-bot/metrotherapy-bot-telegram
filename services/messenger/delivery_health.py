from __future__ import annotations

from typing import Any

from services.messenger import delivery_outbox


def delivery_health_snapshot() -> dict[str, Any]:
    counts = delivery_outbox.outbox_snapshot()
    worker = getattr(delivery_outbox, "_worker_task", None)
    stop_event = getattr(delivery_outbox, "_worker_stop", None)
    expected = stop_event is not None
    active = bool(worker is not None and not worker.done())
    # Before the HTTP runtime starts, this function is also used as a pure config
    # preflight. Once start_delivery_worker() declares the worker expected, a
    # stopped/crashed task becomes a real readiness failure.
    healthy = active if expected else True
    return {
        "worker_expected": expected,
        "worker_active": active,
        "worker_running": healthy,
        "pending": int(counts.get("pending", 0)),
        "retry": int(counts.get("retry", 0)),
        "sending": int(counts.get("sending", 0)),
        "sent": int(counts.get("sent", 0)),
        "dead": int(counts.get("dead", 0)),
    }
