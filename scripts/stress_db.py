from __future__ import annotations

"""Isolated database stress probe.

The probe writes only into stress_probe_events and tags every row with a run_id.
By default it deletes its own rows at the end. It exercises concurrent
insert/select/transaction rollback paths without touching product tables.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("APP_ENV", "prod")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from services.db import db  # noqa: E402


@dataclass(frozen=True)
class DbProbeResult:
    name: str
    ok: bool
    latency_ms: float
    detail: str = ""


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100.0) * (len(ordered) - 1)))))
    return round(float(ordered[index]), 2)


def _ensure_table() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stress_probe_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                worker_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """.strip()
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stress_probe_events_run_id ON stress_probe_events(run_id)")


def _insert_one(run_id: str, worker_id: int, seq: int) -> DbProbeResult:
    started = time.perf_counter()
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO stress_probe_events(run_id, worker_id, seq, payload, created_at)
                VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                """.strip(),
                (run_id, int(worker_id), int(seq), json.dumps({"worker": worker_id, "seq": seq}, sort_keys=True)),
            )
    except (OSError, RuntimeError, ValueError) as exc:
        latency = (time.perf_counter() - started) * 1000.0
        return DbProbeResult("insert", False, round(latency, 2), f"{type(exc).__name__}: {exc}")
    latency = (time.perf_counter() - started) * 1000.0
    return DbProbeResult("insert", True, round(latency, 2))


def _select_count(run_id: str) -> DbProbeResult:
    started = time.perf_counter()
    try:
        with db() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM stress_probe_events WHERE run_id=?", (run_id,)).fetchone()
            count = int(row["c"] if row else 0)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        latency = (time.perf_counter() - started) * 1000.0
        return DbProbeResult("select_count", False, round(latency, 2), f"{type(exc).__name__}: {exc}")
    latency = (time.perf_counter() - started) * 1000.0
    return DbProbeResult("select_count", True, round(latency, 2), f"count={count}")


def _rollback_probe(run_id: str) -> DbProbeResult:
    started = time.perf_counter()
    marker = f"rollback-{uuid.uuid4().hex}"
    try:
        with db() as conn:
            conn.execute("BEGIN")
            conn.execute(
                """
                INSERT INTO stress_probe_events(run_id, worker_id, seq, payload, created_at)
                VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                """.strip(),
                (run_id, -1, -1, marker),
            )
            conn.execute("ROLLBACK")
        with db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM stress_probe_events WHERE run_id=? AND payload=?",
                (run_id, marker),
            ).fetchone()
            count = int(row["c"] if row else 0)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        latency = (time.perf_counter() - started) * 1000.0
        return DbProbeResult("rollback", False, round(latency, 2), f"{type(exc).__name__}: {exc}")
    latency = (time.perf_counter() - started) * 1000.0
    return DbProbeResult("rollback", count == 0, round(latency, 2), f"rolled_back_rows={count}")


def _cleanup(run_id: str) -> DbProbeResult:
    started = time.perf_counter()
    try:
        with db() as conn:
            conn.execute("DELETE FROM stress_probe_events WHERE run_id=?", (run_id,))
    except (OSError, RuntimeError, ValueError) as exc:
        latency = (time.perf_counter() - started) * 1000.0
        return DbProbeResult("cleanup", False, round(latency, 2), f"{type(exc).__name__}: {exc}")
    latency = (time.perf_counter() - started) * 1000.0
    return DbProbeResult("cleanup", True, round(latency, 2))


async def _worker(run_id: str, worker_id: int, operations: int, sem: asyncio.Semaphore) -> list[DbProbeResult]:
    out: list[DbProbeResult] = []
    for seq in range(operations):
        async with sem:
            out.append(await asyncio.to_thread(_insert_one, run_id, worker_id, seq))
        if seq % 10 == 0:
            async with sem:
                out.append(await asyncio.to_thread(_select_count, run_id))
    return out


def _summary(results: list[DbProbeResult]) -> dict[str, Any]:
    by_name: dict[str, list[DbProbeResult]] = {}
    for item in results:
        by_name.setdefault(item.name, []).append(item)
    out: dict[str, Any] = {}
    for name, items in sorted(by_name.items()):
        latencies = [item.latency_ms for item in items]
        failed = [item for item in items if not item.ok]
        out[name] = {
            "total": len(items),
            "ok": len(items) - len(failed),
            "failed": len(failed),
            "avg_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
            "p50_ms": _percentile(latencies, 50),
            "p95_ms": _percentile(latencies, 95),
            "p99_ms": _percentile(latencies, 99),
            "max_ms": round(max(latencies), 2) if latencies else 0.0,
            "sample_failure": asdict(failed[0]) if failed else None,
        }
    return out


async def run(args: argparse.Namespace) -> int:
    run_id = args.run_id or f"stress-{uuid.uuid4().hex}"
    started = time.perf_counter()
    _ensure_table()
    sem = asyncio.Semaphore(args.concurrency)
    batches = await asyncio.gather(*(_worker(run_id, worker_id, args.operations, sem) for worker_id in range(args.workers)))
    results = [item for batch in batches for item in batch]
    results.append(await asyncio.to_thread(_rollback_probe, run_id))
    results.append(await asyncio.to_thread(_select_count, run_id))
    if not args.keep_rows:
        results.append(await asyncio.to_thread(_cleanup, run_id))
    elapsed = round(time.perf_counter() - started, 2)
    summary = _summary(results)
    failed = {name: data for name, data in summary.items() if data["failed"]}
    report = {
        "ok": not failed,
        "run_id": run_id,
        "elapsed_sec": elapsed,
        "workers": args.workers,
        "operations_per_worker": args.operations,
        "concurrency": args.concurrency,
        "kept_rows": bool(args.keep_rows),
        "summary": summary,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--operations", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--keep-rows", action="store_true")
    args = parser.parse_args()
    if args.workers <= 0:
        raise SystemExit("--workers must be > 0")
    if args.operations <= 0:
        raise SystemExit("--operations must be > 0")
    if args.concurrency <= 0:
        raise SystemExit("--concurrency must be > 0")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())