from __future__ import annotations

"""Minimal Support-AI (rules + templates).

Это НЕ "магия" и не тяжёлая модель. Это стабильная интерпретация динамики:
- пропуски → мягкий вход (reentry)
- повтор зоны тела → накопление (accumulation)
- волны по самочувствию → стабилизация (destabilization)
- устойчивые улучшения → закрепление (release)

Задача: ощущение персональной реакции системы + выбор следующего шага.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config.settings import settings
from services.mood import series
from services.subscription import has_access
from services.support_store import (
    BodyAreaObservation,
    fetch_recent_body_area_observations,
    log_reaction,
)
from services.support_templates import render_no_support, render_pre


log = logging.getLogger(__name__)


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
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _parse_day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _configured_timezone() -> ZoneInfo | timezone:
    timezone_name = str(getattr(settings, "TIMEZONE", "Europe/Moscow") or "Europe/Moscow")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        log.error("Configured timezone is unavailable: %s; using UTC", timezone_name)
        return timezone.utc


def _observation_local_day(
    observation: BodyAreaObservation,
    local_tz: ZoneInfo | timezone,
) -> date | None:
    raw = observation.created_at_utc.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        timestamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(local_tz).date()


def _same_area_consecutive_days(
    observations: list[BodyAreaObservation],
) -> tuple[str | None, int]:
    """Count actual adjacent local calendar days, not repeated clicks.

    Observations are newest-first. Multiple answers on one day collapse to the
    newest answer for that day, then the streak must cover adjacent dates.
    """
    if not observations:
        return None, 0

    newest_area = observations[0].area or None
    local_tz = _configured_timezone()
    area_by_day: dict[date, str] = {}
    for observation in observations:
        local_day = _observation_local_day(observation, local_tz)
        area = observation.area.strip()
        if local_day is None or not area or local_day in area_by_day:
            continue
        area_by_day[local_day] = area

    if not area_by_day:
        return newest_area, 1 if newest_area else 0

    latest_day = max(area_by_day)
    target_area = area_by_day[latest_day]
    count = 0
    cursor = latest_day
    while area_by_day.get(cursor) == target_area:
        count += 1
        cursor -= timedelta(days=1)
    return target_area, count


def _skip_count_last_days(rows: list[dict[str, Any]], *, days: int = 5) -> int:
    """Count unfinished scored sessions in the latest calendar-day window.

    Use the newest valid session day as a deterministic anchor so tests, delayed
    jobs and imported history do not depend on the server clock.
    """
    parsed = [(row, _parse_day(row.get("day"))) for row in rows]
    valid_days = [row_day for _, row_day in parsed if row_day is not None]
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
        msg = render_no_support(
            kind_label=(
                "дорога на работу"
                if kind_norm in ("work", "morning")
                else "дорога домой"
                if kind_norm in ("home", "evening")
                else ""
            )
        )
        return SupportDecision(mode="no_support", message=msg)

    # 1) Тело: повтор должен означать реальные соседние календарные дни.
    observations = fetch_recent_body_area_observations(int(user_id), limit=30)
    area, same_area_days = _same_area_consecutive_days(observations)

    # 2) Самочувствие: динамика по последним, а не первым сессиям.
    rows = series(
        int(user_id),
        kind=(kind_norm if kind_norm not in ("both", "") else None),
        limit=40,
    )
    deltas: list[float] = []
    for row in rows[-14:]:
        pre, post = row.get("pre"), row.get("post")
        if pre is None or post is None:
            continue
        deltas.append(float(int(post) - int(pre)))

    avg_delta = _avg(deltas[-7:])
    var_delta = _variance(deltas[-10:])
    skip_count_5d = _skip_count_last_days(rows, days=5)

    # 3) Режимы
    if skip_count_5d >= 1:
        mode = "reentry"
    elif same_area_days >= 3:
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
        same_area_days=int(same_area_days),
        avg_delta_7d=avg_delta,
        skip_count_5d=int(skip_count_5d),
    )

    # best-effort logging
    log_reaction(
        user_id=int(user_id),
        mode=mode,
        area=area,
        note=(
            f"avg={avg_delta}; same_area_days={same_area_days}; "
            f"skips_5d={skip_count_5d}"
        ),
    )
    return SupportDecision(
        mode=mode,
        message=msg,
        area=area,
        same_area_days=int(same_area_days),
        avg_delta_7d=avg_delta,
        skip_count_5d=int(skip_count_5d),
    )
