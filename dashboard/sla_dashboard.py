import logging

import sqlite3, statistics
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)
DB = "data.db"

def load(metric):
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            "SELECT value_ms FROM sla_metrics WHERE metric=?",
            (metric,)
        ).fetchall()
    return [r[0] for r in rows]

def show(metric):
    data = load(metric)
    if not data:
        logger.info("No data for %s", metric)
        return
    p95 = statistics.quantiles(data, n=20)[18]
    p99 = statistics.quantiles(data, n=100)[98]
    plt.hist(data, bins=50)
    plt.axvline(1000, color="red")
    plt.title(f"{metric} p95={p95:.0f}ms p99={p99:.0f}ms")
    plt.show()

if __name__ == "__main__":
    for m in ("start_to_mood", "mood_to_audio", "audio_to_post"):
        show(m)