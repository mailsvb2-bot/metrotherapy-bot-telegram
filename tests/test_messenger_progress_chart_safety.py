from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import services.messenger.progress_charts as charts


def test_with_chart_tempfile_is_closed_reopenable_and_cleaned() -> None:
    chart = charts.MessengerProgressChart(
        title="Progress",
        filename="ignored-name.png",
        data=b"png-bytes",
    )
    observed_path: Path | None = None

    def callback(path: Path) -> bytes:
        nonlocal observed_path
        observed_path = path
        assert path.is_file()
        return path.read_bytes()

    result = charts.with_chart_tempfile(chart, callback)

    assert result == b"png-bytes"
    assert observed_path is not None
    assert not observed_path.exists()
    assert not observed_path.parent.exists()


def test_with_chart_tempfile_cleans_after_callback_error() -> None:
    chart = charts.MessengerProgressChart("Progress", "chart.png", b"png")
    observed_path: Path | None = None

    def callback(path: Path) -> object:
        nonlocal observed_path
        observed_path = path
        assert path.read_bytes() == b"png"
        raise RuntimeError("synthetic-callback-failure")

    with pytest.raises(RuntimeError, match="synthetic-callback-failure"):
        charts.with_chart_tempfile(chart, callback)

    assert observed_path is not None
    assert not observed_path.exists()
    assert not observed_path.parent.exists()


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (True, None),
        ("bad", None),
        (float("nan"), None),
        (float("inf"), None),
        (-11, None),
        (11, None),
        (-10, -10.0),
        ("2.5", 2.5),
        (10, 10.0),
    ],
)
def test_optional_score_is_finite_and_bounded(value: Any, expected: float | None) -> None:
    assert charts._optional_score(value) == expected


class _Figure:
    def __init__(self, payload: bytes = b"atomic-png") -> None:
        self.payload = payload

    def savefig(self, path: Path, *, format: str, dpi: int) -> None:
        assert format == "png"
        assert dpi == 140
        Path(path).write_bytes(self.payload)


def test_atomic_save_replaces_target_and_removes_stage(tmp_path: Path) -> None:
    target = tmp_path / "cache" / "progress.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")

    assert charts._atomic_save_figure(_Figure(), target) is True

    assert target.read_bytes() == b"atomic-png"
    assert not list(target.parent.glob(".*.tmp"))


def test_atomic_save_rejects_empty_stage_and_preserves_existing_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "cache" / "progress.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")

    assert charts._atomic_save_figure(_Figure(b""), target) is False

    assert target.read_bytes() == b"old"
    assert not list(target.parent.glob(".*.tmp"))


class _RowsCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _RowsConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed_sql = ""
        self.params: tuple[Any, ...] = ()

    def execute(self, sql: str, params: tuple[Any, ...]) -> _RowsCursor:
        self.executed_sql = sql
        self.params = params
        return _RowsCursor(self._rows)


def _db_context(rows: list[dict[str, Any]], observed: list[_RowsConnection]):
    @contextmanager
    def factory():
        connection = _RowsConnection(rows)
        observed.append(connection)
        yield connection

    return factory


def test_invalid_scores_are_rejected_before_matplotlib_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[_RowsConnection] = []
    rows = [
        {
            "id": 5,
            "day": "2026-07-20",
            "anchor_id": 1,
            "pre_score": "broken",
            "post_score": float("inf"),
        }
    ]
    monkeypatch.setattr(charts, "DATA_DIR", tmp_path)
    monkeypatch.setattr(charts, "get_db_ro", _db_context(rows, observed))

    result = charts.build_vk_mood_progress_chart_path(123)

    assert result is None
    assert len(observed) == 1
    assert "SELECT id" in observed[0].executed_sql
    assert observed[0].params == (123,)
    assert not (tmp_path / "cache" / "metrotherapy_vk_charts").exists()


def test_cache_directory_uses_canonical_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(charts, "DATA_DIR", tmp_path / "canonical-data")

    result = charts._chart_cache_dir()

    assert result == (tmp_path / "canonical-data" / "cache" / "metrotherapy_vk_charts").resolve()
