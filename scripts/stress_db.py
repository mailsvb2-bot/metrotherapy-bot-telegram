from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Thread

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = SCRIPT_DIR / "stress_db_schema.sql"


@dataclass(frozen=True)
class DbStressReport:
    ok: bool
    db_path: str
    workers: int
    iterations: int
    expected_rows: int
    actual_rows: int
    elapsed_sec: float
    errors: tuple[str, ...]


def _init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path), timeout=30, check_same_thread=False) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()


def _worker(path: Path, *, run_id: str, worker_id: int, iterations: int, errors: list[str]) -> None:
    try:
        conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        try:
            for n in range(iterations):
                conn.execute(
                    "INSERT INTO stress_events(run_id, worker, n) VALUES(?,?,?)",
                    (run_id, int(worker_id), int(n)),
                )
                if n % 50 == 0:
                    conn.commit()
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        errors.append(f"worker={worker_id}:{type(exc).__name__}:{exc}")
    except OSError as exc:
        errors.append(f"worker={worker_id}:{type(exc).__name__}:{exc}")


def run(path: Path, *, workers: int, iterations: int, keep_rows: bool) -> DbStressReport:
    run_id = f"stress-{uuid.uuid4().hex[:12]}"
    _init(path)
    errors: list[str] = []
    started = time.monotonic()
    threads = [
        Thread(target=_worker, args=(path,), kwargs={"run_id": run_id, "worker_id": i, "iterations": iterations, "errors": errors})
        for i in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started

    with sqlite3.connect(str(path), timeout=30, check_same_thread=False) as conn:
        row = conn.execute("SELECT COUNT(*) FROM stress_events WHERE run_id=?", (run_id,)).fetchone()
        actual = int(row[0]) if row else 0
        if not keep_rows:
            conn.execute("DELETE FROM stress_events WHERE run_id=?", (run_id,))
            conn.commit()
    expected = int(workers) * int(iterations)
    return DbStressReport(
        ok=not errors and actual == expected,
        db_path=str(path),
        workers=int(workers),
        iterations=int(iterations),
        expected_rows=expected,
        actual_rows=actual,
        elapsed_sec=round(elapsed, 3),
        errors=tuple(errors),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(Path(tempfile.gettempdir()) / "metrotherapy_stress.db"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=250)
    parser.add_argument("--keep-rows", action="store_true")
    args = parser.parse_args()

    report = run(Path(args.db_path), workers=max(1, args.workers), iterations=max(1, args.iterations), keep_rows=args.keep_rows)
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
