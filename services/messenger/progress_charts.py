from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Callable

from services.mood import series as mood_series
from services.state_ratings import series as state_series
from services.charts import plot_mood, plot_overall, plot_state_ratings


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
    """Expose chart bytes as a temporary PNG path for sender APIs that need files."""
    with tempfile.NamedTemporaryFile(prefix="metrotherapy_", suffix=".png", delete=True) as tmp:
        tmp.write(chart.data)
        tmp.flush()
        return callback(Path(tmp.name))
