from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, Optional, Tuple

from services.db import db

logger = logging.getLogger(__name__)

def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)

def _utc_now_iso() -> str:
    return _utc_now().isoformat()

def compute_and_store_rewards(window_sec: int = 3600, *, lookback_hours: int = 24) -> int:
    """Compute causal rewards for recent decisions.

    Baseline implementation (production-safe):
    - Money reward: sum of payments.amount within [decision_ts, decision_ts+window].
    - State reward: post-pre mood delta plus explicit state ratings in the window.
    - Retention reward: later user activity/progress after the decision.
    Writes into decision_rewards table.

    Returns number of rewards written.
    """
    now = _utc_now()
    since = (now - timedelta(hours=int(lookback_hours))).isoformat()

    written = 0
    with db() as conn:
        # Ensure table exists (schema.ensure should already handle this)
        # Find decisions in extended events table
        try:
            rows = conn.execute(
                """
                SELECT id, user_id, decision_id, correlation_id, COALESCE(timestamp_utc, ts, created_at) AS t
                FROM events
                WHERE decision_id IS NOT NULL AND decision_id != ''
                  AND name='decision_made'
                  AND COALESCE(timestamp_utc, ts, created_at) >= ?
                ORDER BY id DESC
                """,
                (since,),
            ).fetchall()
        except (OSError, ValueError):
            return 0

        for r in rows:
            try:
                _eid, user_id, decision_id, corr_id, t_iso = int(r[0]), int(r[1]), str(r[2]), (str(r[3]) if r[3] is not None else None), str(r[4])
            except (TypeError, ValueError, IndexError):
                continue

            # Idempotency: do not recompute if already exists for this (decision_id, window)
            exists = conn.execute(
                "SELECT 1 FROM decision_rewards WHERE decision_id=? AND window_sec=? LIMIT 1",
                (decision_id, int(window_sec)),
            ).fetchone()
            if exists:
                continue

            # Parse decision time
            try:
                t0 = datetime.fromisoformat(t_iso.replace("Z","+00:00"))
                if t0.tzinfo is None:
                    t0 = t0.replace(tzinfo=timezone.utc)
            except ValueError:
                t0 = now
            except AttributeError:
                t0 = now

            t1 = (t0 + timedelta(seconds=int(window_sec))).isoformat()

            # Money reward: sum payments in window
            money = 0.0
            try:
                prow = conn.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) FROM payments
                    WHERE user_id=? AND created_at >= ? AND created_at <= ?
                    """,
                    (user_id, t0.isoformat(), t1),
                ).fetchone()
                money = float(prow[0] or 0) if prow else 0.0
            except (TypeError, ValueError, IndexError):
                money = 0.0

            state = 0.0
            try:
                mood_row = conn.execute(
                    """
                    SELECT COALESCE(SUM(CASE
                        WHEN pre_score IS NOT NULL AND post_score IS NOT NULL
                        THEN post_score - pre_score ELSE 0 END), 0)
                    FROM mood_sessions
                    WHERE user_id=? AND updated_at_utc >= ? AND updated_at_utc <= ?
                    """,
                    (user_id, t0.isoformat(), t1),
                ).fetchone()
                state += float(mood_row[0] or 0.0) if mood_row else 0.0
            except sqlite3.Error:
                state += 0.0
            except (TypeError, ValueError, IndexError):
                state += 0.0
            try:
                rating_row = conn.execute(
                    """
                    SELECT COALESCE(AVG(rating), 0) FROM state_ratings
                    WHERE user_id=? AND created_at_utc >= ? AND created_at_utc <= ?
                    """,
                    (user_id, t0.isoformat(), t1),
                ).fetchone()
                avg_rating = float(rating_row[0] or 0.0) if rating_row else 0.0
                if avg_rating:
                    # Normalize 1..10 around zero; keep bounded so money remains dominant.
                    state += max(-1.0, min(1.0, (avg_rating - 5.0) / 5.0))
            except sqlite3.Error:
                state += 0.0
            except (TypeError, ValueError, IndexError):
                state += 0.0

            retention = 0.0
            try:
                activity = conn.execute(
                    """
                    SELECT COUNT(*) FROM events
                    WHERE user_id=? AND COALESCE(timestamp_utc, ts, created_at) > ?
                      AND COALESCE(timestamp_utc, ts, created_at) <= ?
                    """,
                    (user_id, t0.isoformat(), t1),
                ).fetchone()
                retention += min(1.0, float(activity[0] or 0) / 3.0) if activity else 0.0
            except sqlite3.Error:
                retention += 0.0
            except (TypeError, ValueError, IndexError):
                retention += 0.0
            try:
                progress = conn.execute(
                    """
                    SELECT COALESCE(MAX(idx), 0) FROM progress
                    WHERE user_id=? AND updated_at >= ? AND updated_at <= ?
                    """,
                    (user_id, t0.isoformat(), t1),
                ).fetchone()
                retention += min(1.0, float(progress[0] or 0) / 10.0) if progress else 0.0
            except sqlite3.Error:
                retention += 0.0
            except (TypeError, ValueError, IndexError):
                retention += 0.0

            reward = money + state + retention
            meta = json.dumps({"money": money, "state": state, "retention": retention}, ensure_ascii=False)

            conn.execute(
                """
                INSERT INTO decision_rewards(
                    decision_id, user_id, correlation_id,
                    reward_value, money_value, state_value, retention_value,
                    window_sec, computed_at_utc, meta
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (decision_id, user_id, corr_id, reward, money, state, retention, int(window_sec), _utc_now_iso(), meta),
            )
            written += 1

        conn.commit()

    if written:
        logger.info("RewardEngine: wrote %s rewards (window=%ss, lookback=%sh)", written, window_sec, lookback_hours)
    return written
