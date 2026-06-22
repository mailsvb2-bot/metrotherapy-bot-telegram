from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from services.db import db


log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class BehaviorSnapshot:
    user_id: int
    ema_delta_ms: float | None
    ema_absdev_ms: float | None
    profile: str | None


def log_interaction(user_id: int, kind: str, key: str | None, delta_ms: int | None) -> None:
    """Append-only interaction log. Uses single connection per call.

    kind: callback/message/command
    key: cb prefix / command name
    delta_ms: time since previous user action (ms)
    """
    ts = _utc_now_iso()
    with db() as conn:
        conn.execute(
            "INSERT INTO interaction_log(user_id, kind, key, delta_ms, ts) VALUES(?,?,?,?,?)",
            (int(user_id), str(kind), (key or None), (int(delta_ms) if delta_ms is not None else None), ts),
        )


def update_behavior(user_id: int, delta_ms: int | None) -> BehaviorSnapshot:
    """Update incremental features for the user.

    We use exponential moving averages to be robust and cheap.
    Profile categories:
      - compressed: very fast / bursty
      - sparse: long pauses
      - stable: normal rhythm
    """
    now = _utc_now_iso()
    alpha = 0.20  # faster adaptation but still stable

    with db() as conn:
        row = conn.execute(
            "SELECT ema_delta_ms, ema_absdev_ms FROM user_behavior WHERE user_id=?",
            (int(user_id),),
        ).fetchone()

        ema = float(row["ema_delta_ms"]) if row and row["ema_delta_ms"] is not None else None
        dev = float(row["ema_absdev_ms"]) if row and row["ema_absdev_ms"] is not None else None

        if delta_ms is None or delta_ms <= 0:
            # no meaningful update
            profile = _classify(ema, dev)
            conn.execute(
                "INSERT OR IGNORE INTO user_behavior(user_id, last_ts, ema_delta_ms, ema_absdev_ms, profile, updated_at) VALUES(?,?,?,?,?,?)",
                (int(user_id), now, ema, dev, profile, now),
            )
            conn.execute(
                "UPDATE user_behavior SET last_ts=?, profile=?, updated_at=? WHERE user_id=?",
                (now, profile, now, int(user_id)),
            )
            return BehaviorSnapshot(int(user_id), ema, dev, profile)

        x = float(delta_ms)
        if ema is None:
            ema = x
            dev = 0.0
        else:
            ema = (1 - alpha) * ema + alpha * x
            dev = (1 - alpha) * (dev if dev is not None else 0.0) + alpha * abs(x - ema)

        profile = _classify(ema, dev)

        conn.execute(
            "INSERT OR IGNORE INTO user_behavior(user_id, last_ts, ema_delta_ms, ema_absdev_ms, profile, updated_at) VALUES(?,?,?,?,?,?)",
            (int(user_id), now, ema, dev, profile, now),
        )
        conn.execute(
            "UPDATE user_behavior SET last_ts=?, ema_delta_ms=?, ema_absdev_ms=?, profile=?, updated_at=? WHERE user_id=?",
            (now, ema, dev, profile, now, int(user_id)),
        )

    return BehaviorSnapshot(int(user_id), ema, dev, profile)


def _classify(ema_delta_ms: Optional[float], ema_absdev_ms: Optional[float]) -> str:
    # Conservative defaults
    if ema_delta_ms is None:
        return "stable"

    # If user rhythm is very fast (<= 700 ms), treat as compressed.
    if ema_delta_ms <= 700:
        return "compressed"

    # Very slow (>= 5000 ms) -> sparse
    if ema_delta_ms >= 5000:
        return "sparse"

    # If extremely bursty, lean to compressed.
    if ema_absdev_ms is not None and ema_absdev_ms >= 2500 and ema_delta_ms < 2500:
        return "compressed"

    return "stable"


def get_behavior(user_id: int) -> BehaviorSnapshot:
    with db() as conn:
        row = conn.execute(
            "SELECT ema_delta_ms, ema_absdev_ms, profile FROM user_behavior WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
    if not row:
        return BehaviorSnapshot(int(user_id), None, None, "stable")
    return BehaviorSnapshot(int(user_id), row["ema_delta_ms"], row["ema_absdev_ms"], row["profile"] or "stable")


def behavior_distribution() -> dict[str, int]:
    """Counts by profile."""
    with db() as conn:
        rows = conn.execute(
            "SELECT profile, COUNT(*) as cnt FROM user_behavior GROUP BY profile"
        ).fetchall()
    out = {"compressed": 0, "sparse": 0, "stable": 0}
    for r in rows:
        p = (r["profile"] or "stable")
        if p not in out:
            out[p] = 0
        out[p] += int(r["cnt"] or 0)
    return out
