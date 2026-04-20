from datetime import datetime
from core.time_utils import utc_now
from services.db import db

def mark_step(user_id: int, step: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO events(user_id, event, ts) VALUES (?, ?, ?)",
            (user_id, f"funnel:{step}", utc_now().isoformat())
        )

def step_done(user_id: int, step: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM events WHERE user_id=? AND event=? LIMIT 1",
            (user_id, f"funnel:{step}")
        ).fetchone()
    return bool(row)

def first_ts_for(user_id: int, event: str) -> str | None:
    with db() as conn:
        row = conn.execute(
            "SELECT ts FROM events WHERE user_id=? AND event=? ORDER BY id ASC LIMIT 1",
            (user_id, event)
        ).fetchone()
    return row["ts"] if row else None
