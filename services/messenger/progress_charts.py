from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import tempfile
from typing import Callable, Any

from services.db import db
from services.mood import series as mood_series
from services.state_ratings import series as state_series
from services.charts import plot_mood, plot_overall, plot_state_ratings

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
    """Expose chart bytes as a temporary PNG path for sender APIs that need files."""
    with tempfile.NamedTemporaryFile(prefix="metrotherapy_", suffix=".png", delete=True) as tmp:
        tmp.write(chart.data)
        tmp.flush()
        return callback(Path(tmp.name))


def build_vk_mood_progress_chart_path(user_id: int) -> Path | None:
    """Build VK/MAX-compatible mood progress chart from canonical mood_sessions."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, day, slot, kind, anchor_id, pre_score, post_score, audio_sent
            FROM mood_sessions
            WHERE user_id=?
              AND (pre_score IS NOT NULL OR post_score IS NOT NULL)
            ORDER BY id ASC
            LIMIT 80
            """,
            (int(user_id),),
        ).fetchall()

    records = [dict(r) for r in rows]
    if not records:
        return None

    labels: list[str] = []
    pre_values: list[float | None] = []
    post_values: list[float | None] = []
    delta_values: list[float | None] = []

    for idx, row in enumerate(records, start=1):
        anchor = row.get("anchor_id")
        day = str(row.get("day") or "")
        label = f"№{anchor}" if anchor not in (None, "") else str(idx)
        if day:
            label = f"{label}\n{day[-5:]}"
        labels.append(label)

        pre = row.get("pre_score")
        post = row.get("post_score")
        pre_f = float(pre) if pre is not None else None
        post_f = float(post) if post is not None else None

        pre_values.append(pre_f)
        post_values.append(post_f)
        delta_values.append((post_f - pre_f) if pre_f is not None and post_f is not None else None)

    uid = int(user_id)
    latest_session_id = max(int(row.get("id") or 0) for row in records)
    out_dir = Path("data/cache/metrotherapy_vk_charts")
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

    x = list(range(1, len(labels) + 1))
    fig, ax = plt.subplots(figsize=(10, 5.5))

    if any(v is not None for v in pre_values):
        ax.plot(x, [v if v is not None else float("nan") for v in pre_values], marker="o", label="До")
    if any(v is not None for v in post_values):
        ax.plot(x, [v if v is not None else float("nan") for v in post_values], marker="o", label="После")
    if any(v is not None for v in delta_values):
        ax.bar(x, [v if v is not None else 0 for v in delta_values], alpha=0.25, label="Изменение")

    ax.axhline(0, linewidth=1)
    ax.set_title("Метротерапия — динамика состояния")
    ax.set_ylabel("Оценка состояния от -10 до +10")
    ax.set_xlabel("Практики")
    ax.set_ylim(-10.5, 10.5)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(loc="best")

    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)

    legacy_path = out_dir / f"progress_{uid}.png"
    stale_paths = [legacy_path, *out_dir.glob(f"progress_{uid}_*.png")]
    for stale_path in stale_paths:
        if stale_path == out_path:
            continue
        try:
            if stale_path.exists() and stale_path.is_file():
                stale_path.unlink()
        except OSError:
            log.warning("VK progress chart stale cache cleanup failed: path=%s", stale_path, exc_info=True)

    log.info("VK progress chart built: user_id=%s path=%s", user_id, out_path)
    return out_path
