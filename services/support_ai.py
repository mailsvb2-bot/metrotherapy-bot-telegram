from __future__ import annotations

"""Minimal Support-AI (rules + templates).

Это НЕ "магия" и не тяжёлая модель. Это стабильная интерпретация динамики:
- пропуски → мягкий вход (reentry)
- повтор зоны тела → накопление (accumulation)
- волны по самочувствию → стабилизация (destabilization)
- устойчивые улучшения → закрепление (release)

Задача: ощущение персональной реакции системы + выбор следующего шага.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from services.mood import series
from services.subscription import has_access
from services.support_store import (
    fetch_recent_body_areas,
    count_same_prefix_streak,
    log_reaction,
)
from services.support_templates import render_pre, render_no_support


@dataclass(frozen=True)
class SupportDecision:
    mode: str
    message: str
    area: Optional[str] = None
    same_area_days: int = 0
    avg_delta_7d: float | None = None
    skip_count_5d: int = 0


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _variance(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return sum((x - m) ** 2 for x in values) / (len(values) - 1)


def _parse_day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _skip_count_last_days(rows: list[dict[str, Any]], *, days: int = 5) -> int:
    """Count unfinished scored sessions in the latest calendar-day window.

    The previous implementation returned only 0/1 and inspected the last fourteen
    rows, despite exposing the field as ``skip_count_5d``. Use the newest valid
    session day as the deterministic anchor so tests, delayed jobs and imported
    history do not depend on the server clock.
    """

    parsed = [(row, _parse_day(row.get("day"))) for row in rows]
    valid_days = [day for _, day in parsed if day is not None]
    if not valid_days:
        fallback = rows[-max(1, int(days)) :]
        return sum(1 for row in fallback if row.get("pre") is not None and row.get("post") is None)

    anchor = max(valid_days)
    cutoff = anchor - timedelta(days=max(1, int(days)) - 1)
    return sum(
        1
        for row, row_day in parsed
        if row_day is not None
        and cutoff <= row_day <= anchor
        and row.get("pre") is not None
        and row.get("post") is None
    )


def decide_support_pre(
    *,
    user_id: int,
    kind: str,
    require_subscription: bool = True,
) -> SupportDecision:
    """Решение сопровождения перед аудио.

    kind: work/home/morning/evening/both (нормализация внутри has_access)
    """

    kind_norm = (kind or "").strip().lower() or "both"

    if require_subscription and not has_access(int(user_id), kind_norm):
        msg = render_no_support(kind_label=("дорога на работу" if kind_norm in ("work", "morning") else "дорога домой" if kind_norm in ("home", "evening") else ""))
        return SupportDecision(mode="no_support", message=msg)

    # 1) Тело: берём последние ответы и ищем «повтор зоны»
    areas = fetch_recent_body_areas(int(user_id), limit=8)
    area = areas[0] if areas else None
    same_area = count_same_prefix_streak(areas) if areas else 0

    # 2) Самочувствие: динамика по последним сессиям
    rows = series(int(user_id), kind=(kind_norm if kind_norm not in ("both", "") else None), limit=40)
    deltas: list[float] = []
    for r in rows[-14:]:
        pre, post = r.get("pre"), r.get("post")
        if pre is None or post is None:
            continue
        deltas.append(float(int(post) - int(pre)))

    avg_delta = _avg(deltas[-7:])
    var_delta = _variance(deltas[-10:])
    skip_count_5d = _skip_count_last_days(rows, days=5)

    # 3) Режимы
    if skip_count_5d >= 1:
        mode = "reentry"
    elif same_area >= 3:
        mode = "accumulation"
    elif var_delta is not None and var_delta >= 3.0:
        mode = "destabilization"
    elif avg_delta is not None and avg_delta >= 1.0:
        mode = "release"
    else:
        mode = "baseline"

    msg = render_pre(
        mode=mode,
        area=area,
        same_area_days=int(same_area),
        avg_delta_7d=avg_delta,
        skip_count_5d=int(skip_count_5d),
    )

    # best-effort logging
    log_reaction(user_id=int(user_id), mode=mode, area=area, note=f"avg={avg_delta}; same_area={same_area}; skips_5d={skip_count_5d}")
    return SupportDecision(
        mode=mode,
        message=msg,
        area=area,
        same_area_days=int(same_area),
        avg_delta_7d=avg_delta,
        skip_count_5d=int(skip_count_5d),
    )
