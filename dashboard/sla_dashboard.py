from __future__ import annotations

"""Read-only SLA reporting with optional, explicitly requested plotting."""

import argparse
import json
import logging
import math
import sqlite3
import statistics
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_METRICS = ("start_to_mood", "mood_to_audio", "audio_to_post")
DEFAULT_THRESHOLD_MS = 1000.0
DEFAULT_LIMIT = 100_000
MAX_LIMIT = 1_000_000

logger = logging.getLogger(__name__)


class SlaDashboardError(RuntimeError):
    """Expected dashboard validation or storage failure with a safe code."""

    def __init__(self, code: str) -> None:
        normalized = str(code or "sla_dashboard_failed")
        super().__init__(normalized)
        self.code = normalized


def _validate_metric(metric: str) -> str:
    normalized = str(metric or "").strip()
    if not normalized:
        raise SlaDashboardError("metric_required")
    if len(normalized) > 128:
        raise SlaDashboardError("metric_too_long")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise SlaDashboardError("metric_contains_control_character")
    return normalized


def _validate_threshold(value: float) -> float:
    threshold = float(value)
    if not math.isfinite(threshold):
        raise SlaDashboardError("threshold_must_be_finite")
    if threshold < 0:
        raise SlaDashboardError("threshold_must_be_non_negative")
    return threshold


def _bounded_limit(value: int) -> int:
    return max(1, min(int(value), MAX_LIMIT))


def _sqlite_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.is_file():
        raise SlaDashboardError("sqlite_file_not_found")
    return sqlite3.connect(
        resolved.as_uri() + "?mode=ro",
        uri=True,
        timeout=10,
        check_same_thread=False,
    )


def _query_rows(metric: str, limit: int) -> Sequence[Sequence[Any]]:
    from core.paths import DB_PATH
    from services.db.read_only import get_db_ro
    from services.db.runtime import is_postgres_enabled

    sql = (
        "SELECT value_ms FROM sla_metrics "
        "WHERE metric=? ORDER BY ts DESC LIMIT ?"
    )
    params = (metric, int(limit))
    try:
        if is_postgres_enabled():
            with get_db_ro() as conn:
                return list(conn.execute(sql, params).fetchall())
        with closing(_sqlite_readonly(Path(DB_PATH))) as conn:
            return list(conn.execute(sql, params).fetchall())
    except SlaDashboardError:
        raise
    except sqlite3.Error as exc:
        raise SlaDashboardError(f"storage_query_failed:{type(exc).__name__}") from None
    except OSError as exc:
        raise SlaDashboardError(f"storage_query_failed:{type(exc).__name__}") from None


def _clean_values(rows: Iterable[Sequence[Any]]) -> tuple[list[float], int]:
    values: list[float] = []
    invalid_rows = 0
    for row in rows:
        raw = row[0] if row else None
        if isinstance(raw, bool):
            invalid_rows += 1
            continue
        try:
            value = float(raw)
        except TypeError:
            invalid_rows += 1
            continue
        except ValueError:
            invalid_rows += 1
            continue
        if not math.isfinite(value) or value < 0:
            invalid_rows += 1
            continue
        values.append(value)
    return values, invalid_rows


def percentile(values: Sequence[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    fraction = min(max(float(percent), 0.0), 100.0) / 100.0
    rank = (len(ordered) - 1) * fraction
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def load(metric: str, *, limit: int = DEFAULT_LIMIT) -> list[float]:
    """Compatibility API: return valid non-negative finite values only."""

    normalized = _validate_metric(metric)
    rows = _query_rows(normalized, _bounded_limit(limit))
    values, _invalid_rows = _clean_values(rows)
    return values


def build_report(
    metric: str,
    *,
    threshold_ms: float = DEFAULT_THRESHOLD_MS,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    normalized = _validate_metric(metric)
    threshold = _validate_threshold(threshold_ms)
    bounded_limit = _bounded_limit(limit)
    rows = _query_rows(normalized, bounded_limit)
    values, invalid_rows = _clean_values(rows)
    sample_count = len(values)
    breaches = sum(1 for value in values if value > threshold)

    return {
        "ok": True,
        "metric": normalized,
        "limit": bounded_limit,
        "threshold_ms": threshold,
        "sample_count": sample_count,
        "invalid_rows": invalid_rows,
        "min_ms": min(values) if values else None,
        "max_ms": max(values) if values else None,
        "mean_ms": statistics.fmean(values) if values else None,
        "median_ms": statistics.median(values) if values else None,
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
        "threshold_breaches": breaches,
        "breach_rate": (breaches / sample_count) if sample_count else 0.0,
        "values": values,
        "error_code": "",
    }


def _error_report(metric: str, code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "metric": str(metric or ""),
        "sample_count": 0,
        "invalid_rows": 0,
        "threshold_breaches": 0,
        "breach_rate": 0.0,
        "values": [],
        "error_code": str(code or "sla_dashboard_failed"),
    }


def _plot_data(metric: str, values: Sequence[float], threshold_ms: float) -> None:
    import matplotlib.pyplot as plt

    if not values:
        logger.info("No data for %s", metric)
        return
    p95 = percentile(values, 95)
    p99 = percentile(values, 99)
    plt.hist(values, bins=min(50, max(1, len(values))))
    plt.axvline(threshold_ms, color="red")
    plt.title(f"{metric} p95={p95:.0f}ms p99={p99:.0f}ms")
    plt.show()


def show(metric: str) -> None:
    """Compatibility API: explicitly plot one metric."""

    values = load(metric)
    _plot_data(_validate_metric(metric), values, DEFAULT_THRESHOLD_MS)


def _public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "values"}


def _print_human(report: dict[str, Any]) -> None:
    if report.get("ok") is not True:
        print(
            "SLA_REPORT "
            f"metric={report.get('metric')} ok=false error_code={report.get('error_code')}"
        )
        return
    print(
        "SLA_REPORT "
        f"metric={report.get('metric')} ok=true samples={report.get('sample_count')} "
        f"invalid={report.get('invalid_rows')} p95_ms={report.get('p95_ms')} "
        f"p99_ms={report.get('p99_ms')} breaches={report.get('threshold_breaches')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only SLA report")
    parser.add_argument("--metric", action="append", default=[])
    parser.add_argument("--threshold-ms", type=float, default=DEFAULT_THRESHOLD_MS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    metrics = tuple(args.metric) if args.metric else DEFAULT_METRICS
    reports: list[dict[str, Any]] = []
    for metric in metrics:
        try:
            report = build_report(
                metric,
                threshold_ms=args.threshold_ms,
                limit=args.limit,
            )
        except SlaDashboardError as exc:
            report = _error_report(metric, exc.code)
        reports.append(report)
        if args.plot and report.get("ok") is True:
            _plot_data(
                str(report["metric"]),
                list(report.get("values") or []),
                float(report["threshold_ms"]),
            )

    public_reports = [_public_report(report) for report in reports]
    payload: dict[str, Any] = {
        "ok": all(report.get("ok") is True for report in public_reports),
        "reports": public_reports,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False))
    else:
        for report in public_reports:
            _print_human(report)
    if args.strict and payload["ok"] is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
