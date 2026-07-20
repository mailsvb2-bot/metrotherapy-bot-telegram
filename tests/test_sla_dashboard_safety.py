from __future__ import annotations

import builtins
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from dashboard import sla_dashboard as dashboard


def _write_sla_db(path: Path, rows: list[tuple[str, Any, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "CREATE TABLE sla_metrics("
            "user_id INTEGER, metric TEXT NOT NULL, value_ms, ts REAL NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO sla_metrics(user_id, metric, value_ms, ts) VALUES(?,?,?,?)",
            [(1, metric, value, ts) for metric, value, ts in rows],
        )
        conn.commit()


def _use_sqlite(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    import core.paths as paths
    import services.db.runtime as runtime

    monkeypatch.setattr(paths, "DB_PATH", path)
    monkeypatch.setattr(runtime, "is_postgres_enabled", lambda: False)


def test_percentile_handles_empty_single_and_interpolation() -> None:
    assert dashboard.percentile([], 95) is None
    assert dashboard.percentile([125.0], 95) == 125.0
    assert dashboard.percentile([0.0, 100.0], 95) == pytest.approx(95.0)
    assert dashboard.percentile([0.0, 100.0], -50) == 0.0
    assert dashboard.percentile([0.0, 100.0], 150) == 100.0


def test_clean_values_filters_invalid_non_finite_and_negative_rows() -> None:
    values, invalid = dashboard._clean_values(
        [
            (100,),
            ("250.5",),
            (True,),
            (None,),
            ("bad",),
            (-1,),
            (float("nan"),),
            (float("inf"),),
            (),
        ]
    )

    assert values == [100.0, 250.5]
    assert invalid == 7


def test_single_sample_report_is_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "_query_rows", lambda metric, limit: [(321,)])

    report = dashboard.build_report("mood_to_audio", threshold_ms=300)

    assert report["ok"] is True
    assert report["sample_count"] == 1
    assert report["mean_ms"] == 321.0
    assert report["median_ms"] == 321.0
    assert report["p50_ms"] == 321.0
    assert report["p95_ms"] == 321.0
    assert report["p99_ms"] == 321.0
    assert report["threshold_breaches"] == 1
    assert report["breach_rate"] == 1.0


def test_empty_report_is_successful_and_has_null_statistics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard, "_query_rows", lambda metric, limit: [])

    report = dashboard.build_report("audio_to_post")

    assert report["ok"] is True
    assert report["sample_count"] == 0
    assert report["min_ms"] is None
    assert report["max_ms"] is None
    assert report["mean_ms"] is None
    assert report["median_ms"] is None
    assert report["p50_ms"] is None
    assert report["p95_ms"] is None
    assert report["p99_ms"] is None
    assert report["threshold_breaches"] == 0
    assert report["breach_rate"] == 0.0


def test_sqlite_query_is_read_only_and_does_not_create_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sla.db"
    _write_sla_db(
        path,
        [
            ("mood_to_audio", 120, 1.0),
            ("mood_to_audio", 80, 2.0),
            ("other", 999, 3.0),
        ],
    )
    _use_sqlite(monkeypatch, path)

    report = dashboard.build_report("mood_to_audio", limit=2)

    assert report["ok"] is True
    assert report["values"] == [80.0, 120.0]
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def test_missing_sqlite_is_not_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "missing.db"
    _use_sqlite(monkeypatch, path)

    with pytest.raises(dashboard.SlaDashboardError) as exc:
        dashboard.build_report("mood_to_audio")

    assert exc.value.code == "sqlite_file_not_found"
    assert not path.exists()
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def test_corrupt_sqlite_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "private-db-marker"
    path = tmp_path / f"{marker}.db"
    path.write_bytes(b"not-a-sqlite-database")
    _use_sqlite(monkeypatch, path)

    with pytest.raises(dashboard.SlaDashboardError) as exc:
        dashboard.build_report("mood_to_audio")

    assert exc.value.code.startswith("storage_query_failed:")
    assert marker not in exc.value.code
    assert "not a database" not in exc.value.code.lower()


def test_metric_threshold_and_limit_validation() -> None:
    with pytest.raises(dashboard.SlaDashboardError, match="metric_required"):
        dashboard._validate_metric(" ")
    with pytest.raises(
        dashboard.SlaDashboardError,
        match="metric_contains_control_character",
    ):
        dashboard._validate_metric("bad\nmetric")
    with pytest.raises(dashboard.SlaDashboardError, match="metric_too_long"):
        dashboard._validate_metric("x" * 129)
    with pytest.raises(
        dashboard.SlaDashboardError,
        match="threshold_must_be_non_negative",
    ):
        dashboard._validate_threshold(-1)
    with pytest.raises(
        dashboard.SlaDashboardError,
        match="threshold_must_be_finite",
    ):
        dashboard._validate_threshold(float("nan"))
    with pytest.raises(
        dashboard.SlaDashboardError,
        match="threshold_must_be_finite",
    ):
        dashboard._validate_threshold(float("inf"))

    assert dashboard._bounded_limit(-10) == 1
    assert dashboard._bounded_limit(5) == 5
    assert dashboard._bounded_limit(9_999_999) == dashboard.MAX_LIMIT


def test_regular_cli_does_not_import_matplotlib_or_plot(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        dashboard,
        "build_report",
        lambda metric, threshold_ms, limit: {
            "ok": True,
            "metric": metric,
            "limit": limit,
            "threshold_ms": threshold_ms,
            "sample_count": 1,
            "invalid_rows": 0,
            "min_ms": 100.0,
            "max_ms": 100.0,
            "mean_ms": 100.0,
            "median_ms": 100.0,
            "p50_ms": 100.0,
            "p95_ms": 100.0,
            "p99_ms": 100.0,
            "threshold_breaches": 0,
            "breach_rate": 0.0,
            "values": [100.0],
            "error_code": "",
        },
    )
    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("matplotlib"):
            raise AssertionError("matplotlib imported in report-only mode")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(
        dashboard,
        "_plot_data",
        lambda *args, **kwargs: pytest.fail("plot called without --plot"),
    )

    assert dashboard.main(["--metric", "mood_to_audio", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["reports"][0]["metric"] == "mood_to_audio"
    assert "values" not in payload["reports"][0]


def test_strict_cli_returns_nonzero_with_sanitized_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "secret-storage-message"

    def failed_report(metric: str, *, threshold_ms: float, limit: int) -> dict[str, Any]:
        del metric, threshold_ms, limit
        raise dashboard.SlaDashboardError("storage_query_failed:OperationalError")

    monkeypatch.setattr(dashboard, "build_report", failed_report)

    assert dashboard.main(["--metric", marker, "--json", "--strict"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    report = payload["reports"][0]
    assert report["error_code"] == "storage_query_failed:OperationalError"
    assert marker not in report["error_code"]
    assert "values" not in report


def test_json_payload_never_contains_non_finite_numbers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        dashboard,
        "_query_rows",
        lambda metric, limit: [(100,), (float("nan"),), (float("inf"),)],
    )

    assert dashboard.main(["--metric", "mood_to_audio", "--json"]) == 0
    raw = capsys.readouterr().out
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["reports"][0]["invalid_rows"] == 2
    assert "NaN" not in raw
    assert "Infinity" not in raw
    assert all(
        not isinstance(value, float) or math.isfinite(value)
        for value in payload["reports"][0].values()
    )
