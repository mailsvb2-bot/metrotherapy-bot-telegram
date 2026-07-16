from __future__ import annotations

from typing import Any

from services.messenger import delivery_outbox


def delivery_health_snapshot() -> dict[str, Any]:
    counts = delivery_outbox.outbox_snapshot()
    worker = getattr(delivery_outbox, "_worker_task", None)
    running = bool(worker is not None and not worker.done())
    return {
        "worker_running": running,
        "pending": int(counts.get("pending", 0)),
        "retry": int(counts.get("retry", 0)),
        "sending": int(counts.get("sending", 0)),
        "sent": int(counts.get("sent", 0)),
        "dead": int(counts.get("dead", 0)),
    }
