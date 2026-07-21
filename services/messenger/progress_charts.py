from __future__ import annotations

import logging
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.paths import DATA_DIR
from services.charts import plot_mood, plot_overall, plot_state_ratings
from services.db.read_only import get_db_ro
from services.mood import series as mood_series
from services.state_ratings import series as state_series

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MessengerProgressChart:
    title: str
    filename: str
    data: bytes


def build_progress_charts(user_id: int) -> list[MessengerProgressChart]:
    """Build the same progress-analysis chart set used by Telegram.

    This is intentionally messenger-neutral: Telegram may send the returned bytes
    as photos, VK may upload them as documents, and MAX can later get its own
    native media sender without duplicating analytics logic.
    """
    uid = int(user_id)
    charts: list[MessengerProgressChart] = []

    rows_state = state_series(uid, limit=400)
    rows_work = mood_series(uid, kind="work")
    rows_home = mood_series(uid, kind="home")

    if rows_state:
        charts.append(
            MessengerProgressChart(
                title="📈 Состояние",
                filename="metrotherapy_state.png",
                data=plot_state_ratings("Состояние", rows_state),
            )
        )

    if rows_work:
        charts.append(
            MessengerProgressChart(
                title="📈 Дорога на работу",
                filename="metrotherapy_work.png",
                data=plot_mood("Дорога на работу", rows_work),
            )
        )

    if rows_home:
        charts.append(
            MessengerProgressChart(
                title="📈 Дорога домой",
                filename="metrotherapy_home.png",
                data=plot_mood("Дорога домой", rows_home),
            )
        )

    if rows_work and rows_home:
        charts.append(
            MessengerProgressChart(
                title="📈 Общая динамика",
                filename="metrotherapy_overall.png",
                data=plot_overall(rows_work, rows_home),
            )
        )

    return charts


def with_chart_tempfile(chart: MessengerProgressChart, callback: Callable[[Path], object]) -> object:
    """Expose closed temporary PNG path for sender APIs, including Windows.

    ``NamedTemporaryFile(delete=True)`` keeps the handle open and prevents many
    Windows clients from reopening the path. A private temporary directory lets
    the callback open the file normally while still guaranteeing cleanup after it
    returns or raises.
    """

    with tempfile.TemporaryDirectory(prefix="metrotherapy_chart_") as tmp_dir:
        path = Path(tmp_dir) / "chart.png"
        path.write_bytes(bytes(chart.data))
        return callback(path)


def _optional_score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except TypeError:
        return None
    except ValueError:
        return None
    if not math.isfinite(score) or score < -10 or score > 10:
        return None
    return score


def _chart_cache_dir() -> Path:
    return (Path(DATA_DIR) / "cache" / "metrotherapy_vk_charts").resolve()


def _latest_session_id(records: list[dict[str, Any]]) -> int:
    session_ids: list[int] = []
    for row in records:
        try:
            session_id = int(row.get("id") or 0)
        except TypeError:
            continue
        except ValueError:
            continue
        if session_id > 0:
            session_ids.append(session_id)
    return max(session_ids, default=len(records))


def _atomic_save_figure(figure: Any, out_path: Path) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{out_path.stem}.",
            suffix=".tmp",
            dir=str(out_path.parent),
            delete=False,
        ) as tmp:
            stage_path = Path(tmp.name)
        figure.savefig(stage_path, format="png", dpi=140)
        if not stage_path.is_file() or stage_path.stat().st_size <= 0:
            raise OSError("chart_stage_empty")
        os.replace(stage_path, out_path)
        stage_path = None
        try:
            out_path.chmod(0o600)
        except OSError:
            log.warning("Progress chart permission hardening failed: path=%s", out_path, exc_info=True)
        return True
    except OSError:
        log.exception("Progress chart atomic save failed: path=%s", out_path)
        return False
    finally:
        if stage_path is not None:
            try:
                stage_path.unlink(missing_ok=True)
            except OSError:
                log.warning("Progress chart stage cleanup failed: path=%s", stage_path, exc_info=True)


def build_vk_mood_progress_chart_path(user_id: int) -> Path | None:
    """Build VK/MAX-compatible mood progress chart from newest sessions."""
    with get_db_ro() as conn:
        rows = conn.execute(
            """
            SELECT id, day, slot, kind, anchor_id, pre_score, post_score, audio_sent
            FROM mood_sessions
            WHERE user_id=?
              AND (pre_score IS NOT NULL OR post_score IS NOT NULL)
            ORDER BY id DESC
            LIMIT 80
            """,
            (int(user_id),),
        ).fetchall()

    # Select the newest bounded window in SQL, then restore chronological order
    # for labels and plots. This keeps cache keys moving after the 80th session.
    records = [dict(row) for row in reversed(rows)]
    if not records:
        return None

    labels: list[str] = []
    pre_values: list[float | None] = []
    post_values: list[float | None] = []
    delta_values: list[float | None] = []

    for index, row in enumerate(records, start=1):
        anchor = row.get("anchor_id")
        day = str(row.get("day") or "")
        label = f"№{anchor}" if anchor not in (None, "") else str(index)
        if day:
            label = f"{label}\n{day[-5:]}"
        labels.append(label)

        pre_score = _optional_score(row.get("pre_score"))
        post_score = _optional_score(row.get("post_score"))
        pre_values.append(pre_score)
        post_values.append(post_score)
        delta_values.append(
            (post_score - pre_score)
            if pre_score is not None and post_score is not None
            else None
        )

    if not any(value is not None for value in pre_values + post_values):
        log.warning("VK progress chart skipped: no valid finite scores user_id=%s", user_id)
        return None

    uid = int(user_id)
    latest_session_id = _latest_session_id(records)
    out_dir = _chart_cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"progress_{uid}_{latest_session_id}_{len(records)}.png"
    if out_path.exists() and out_path.is_file() and out_path.stat().st_size > 0:
        log.info("VK progress chart cache hit: user_id=%s path=%s", user_id, out_path)
        return out_path

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        log.exception("VK progress chart: matplotlib unavailable")
        return None

    x_values = list(range(1, len(labels) + 1))
    figure, axis = plt.subplots(figsize=(10, 5.5))
    saved = False
    try:
        if any(value is not None for value in pre_values):
            axis.plot(
                x_values,
                [value if value is not None else float("nan") for value in pre_values],
                marker="o",
                label="До",
            )
        if any(value is not None for value in post_values):
            axis.plot(
                x_values,
                [value if value is not None else float("nan") for value in post_values],
                marker="o",
                label="После",
            )
        if any(value is not None for value in delta_values):
            axis.bar(
                x_values,
                [value if value is not None else 0 for value in delta_values],
                alpha=0.25,
                label="Изменение",
            )

        axis.axhline(0, linewidth=1)
        axis.set_title("Метротерапия — динамика состояния")
        axis.set_ylabel("Оценка состояния от -10 до +10")
        axis.set_xlabel("Практики")
        axis.set_ylim(-10.5, 10.5)
        axis.set_xticks(x_values)
        axis.set_xticklabels(labels)
        axis.grid(True, axis="y", alpha=0.3)
        axis.legend(loc="best")

        figure.tight_layout()
        saved = _atomic_save_figure(figure, out_path)
    except RuntimeError:
        log.exception("VK progress chart rendering failed: user_id=%s", user_id)
    except ValueError:
        log.exception("VK progress chart data rejected: user_id=%s", user_id)
    finally:
        plt.close(figure)

    if not saved:
        return None

    legacy_path = out_dir / f"progress_{uid}.png"
    stale_paths = [legacy_path, *out_dir.glob(f"progress_{uid}_*.png")]
    for stale_path in stale_paths:
        if stale_path == out_path:
            continue
        try:
            if stale_path.exists() and stale_path.is_file():
                stale_path.unlink()
        except OSError:
            log.warning(
                "VK progress chart stale cache cleanup failed: path=%s",
                stale_path,
                exc_info=True,
            )

    log.info("VK progress chart built: user_id=%s path=%s", user_id, out_path)
    return out_path
