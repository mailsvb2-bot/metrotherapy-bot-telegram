from __future__ import annotations

"""Try-before-buy outcome analytics.

This module derives trial quality from existing canonical tables instead of
creating a second funnel state machine.  It intentionally reads from:
- demo_events: free demo delivery/ack ledger;
- mood_sessions: pre/post outcome evidence for source='demo';
- events/payments: conversion evidence already emitted by the bot.
"""

from typing import Any

from services.db import db


def _pct(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return round(float(part) * 100.0 / float(whole), 1)


def trial_outcome_summary() -> dict[str, Any]:
    """Return aggregate outcome quality for free demo sessions.

    Positive/neutral/negative are based on the user's own pre/post scores:
    delta = post_score - pre_score.
    """

    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM mood_sessions WHERE source='demo'"
        ).fetchone()["c"]
        with_pre = conn.execute(
            "SELECT COUNT(*) AS c FROM mood_sessions WHERE source='demo' AND pre_score IS NOT NULL"
        ).fetchone()["c"]
        completed = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM mood_sessions
            WHERE source='demo' AND pre_score IS NOT NULL AND post_score IS NOT NULL
            """
        ).fetchone()["c"]
        positive = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM mood_sessions
            WHERE source='demo' AND pre_score IS NOT NULL AND post_score IS NOT NULL
              AND (post_score - pre_score) > 0
            """
        ).fetchone()["c"]
        neutral = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM mood_sessions
            WHERE source='demo' AND pre_score IS NOT NULL AND post_score IS NOT NULL
              AND (post_score - pre_score) = 0
            """
        ).fetchone()["c"]
        negative = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM mood_sessions
            WHERE source='demo' AND pre_score IS NOT NULL AND post_score IS NOT NULL
              AND (post_score - pre_score) < 0
            """
        ).fetchone()["c"]
        avg_delta = conn.execute(
            """
            SELECT AVG(post_score - pre_score) AS a
            FROM mood_sessions
            WHERE source='demo' AND pre_score IS NOT NULL AND post_score IS NOT NULL
            """
        ).fetchone()["a"]

        by_kind_rows = conn.execute(
            """
            SELECT kind,
                   COUNT(*) AS total,
                   SUM(CASE WHEN pre_score IS NOT NULL THEN 1 ELSE 0 END) AS with_pre,
                   SUM(CASE WHEN pre_score IS NOT NULL AND post_score IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN pre_score IS NOT NULL AND post_score IS NOT NULL AND (post_score - pre_score) > 0 THEN 1 ELSE 0 END) AS positive,
                   SUM(CASE WHEN pre_score IS NOT NULL AND post_score IS NOT NULL AND (post_score - pre_score) = 0 THEN 1 ELSE 0 END) AS neutral,
                   SUM(CASE WHEN pre_score IS NOT NULL AND post_score IS NOT NULL AND (post_score - pre_score) < 0 THEN 1 ELSE 0 END) AS negative,
                   AVG(CASE WHEN pre_score IS NOT NULL AND post_score IS NOT NULL THEN (post_score - pre_score) END) AS avg_delta
            FROM mood_sessions
            WHERE source='demo'
            GROUP BY kind
            """
        ).fetchall()

    by_kind: dict[str, dict[str, Any]] = {}
    for row in by_kind_rows:
        kind = str(row["kind"] or "unknown")
        kind_total = int(row["total"] or 0)
        kind_completed = int(row["completed"] or 0)
        kind_positive = int(row["positive"] or 0)
        by_kind[kind] = {
            "total": kind_total,
            "with_pre": int(row["with_pre"] or 0),
            "completed": kind_completed,
            "positive": kind_positive,
            "neutral": int(row["neutral"] or 0),
            "negative": int(row["negative"] or 0),
            "avg_delta": round(float(row["avg_delta"]), 2) if row["avg_delta"] is not None else None,
            "completion_pct": _pct(kind_completed, kind_total),
            "positive_pct": _pct(kind_positive, kind_completed),
        }

    total_i = int(total or 0)
    completed_i = int(completed or 0)
    positive_i = int(positive or 0)
    return {
        "total_sessions": total_i,
        "with_pre": int(with_pre or 0),
        "completed": completed_i,
        "positive": positive_i,
        "neutral": int(neutral or 0),
        "negative": int(negative or 0),
        "avg_delta": round(float(avg_delta), 2) if avg_delta is not None else None,
        "completion_pct": _pct(completed_i, total_i),
        "positive_pct": _pct(positive_i, completed_i),
        "by_kind": by_kind,
    }


def trial_latest_outcome(user_id: int) -> dict[str, Any] | None:
    """Return latest completed demo mood outcome for a user."""

    with db() as conn:
        row = conn.execute(
            """
            SELECT id, kind, pre_score, post_score, created_at_utc, updated_at_utc
            FROM mood_sessions
            WHERE user_id=? AND source='demo'
              AND pre_score IS NOT NULL AND post_score IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(user_id),),
        ).fetchone()
    if not row:
        return None
    pre = int(row["pre_score"])
    post = int(row["post_score"])
    delta = post - pre
    if delta > 0:
        quality = "positive"
    elif delta < 0:
        quality = "negative"
    else:
        quality = "neutral"
    return {
        "session_id": int(row["id"]),
        "kind": str(row["kind"] or ""),
        "pre": pre,
        "post": post,
        "delta": delta,
        "quality": quality,
        "created_at_utc": row["created_at_utc"],
        "updated_at_utc": row["updated_at_utc"],
    }


def trial_conversion_summary() -> dict[str, Any]:
    """Return high-level trial-to-payment conversion counters."""

    with db() as conn:
        demo_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS c FROM demo_events"
        ).fetchone()["c"]
        ack_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS c FROM demo_events WHERE ack_at_utc IS NOT NULL"
        ).fetchone()["c"]
        outcome_users = conn.execute(
            """
            SELECT COUNT(DISTINCT user_id) AS c
            FROM mood_sessions
            WHERE source='demo' AND pre_score IS NOT NULL AND post_score IS NOT NULL
            """
        ).fetchone()["c"]
        positive_users = conn.execute(
            """
            SELECT COUNT(DISTINCT user_id) AS c
            FROM mood_sessions
            WHERE source='demo' AND pre_score IS NOT NULL AND post_score IS NOT NULL
              AND (post_score - pre_score) > 0
            """
        ).fetchone()["c"]
        paid_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS c FROM payments WHERE payload NOT LIKE ?",
            ("gift:%",),
        ).fetchone()["c"]

    demo_i = int(demo_users or 0)
    ack_i = int(ack_users or 0)
    outcome_i = int(outcome_users or 0)
    positive_i = int(positive_users or 0)
    paid_i = int(paid_users or 0)
    return {
        "demo_users": demo_i,
        "ack_users": ack_i,
        "outcome_users": outcome_i,
        "positive_users": positive_i,
        "paid_users": paid_i,
        "ack_from_demo_pct": _pct(ack_i, demo_i),
        "outcome_from_ack_pct": _pct(outcome_i, ack_i),
        "positive_from_outcome_pct": _pct(positive_i, outcome_i),
        "paid_from_demo_pct": _pct(paid_i, demo_i),
        "paid_from_outcome_pct": _pct(paid_i, outcome_i),
    }
