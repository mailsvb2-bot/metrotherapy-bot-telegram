
import sqlite3, statistics, time, logging

log = logging.getLogger(__name__)
WINDOW_SEC = 300

def analyze(conn):
    now = time.time()
    rows = conn.execute(
        "SELECT metric, value_ms FROM sla_metrics WHERE ts > ?",
        (now - WINDOW_SEC,)
    ).fetchall()

    by_metric = {}
    for m, v in rows:
        by_metric.setdefault(m, []).append(v)

    status = "OK"
    for m, vals in by_metric.items():
        if len(vals) < 10:
            continue
        p95 = statistics.quantiles(vals, n=20)[18]
        p99 = statistics.quantiles(vals, n=100)[98]
        base = statistics.median(vals)
        if p99 > 1200:
            log.error("UX CRITICAL %s p99=%s", m, p99)
            status = "CRITICAL"
        elif p95 > base * 1.4:
            log.warning("UX DEGRADING %s p95=%s", m, p95)
            status = "DEGRADING"
    return status
