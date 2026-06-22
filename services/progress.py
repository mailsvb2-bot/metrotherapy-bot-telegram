from datetime import datetime
from core.time_utils import utc_now
from services.db import db

def get_index(user_id: int, scope: str) -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT idx FROM progress WHERE user_id=? AND scope=?",
            (user_id, scope)
        ).fetchone()
    return int(row["idx"]) if row else 0

def set_index(user_id: int, scope: str, idx: int):
    with db() as conn:
        conn.execute("""
        INSERT INTO progress(user_id, scope, idx, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(user_id, scope) DO UPDATE SET
            idx=excluded.idx,
            updated_at=excluded.updated_at
        """, (user_id, scope, int(idx), utc_now().isoformat()))

def advance(user_id: int, scope: str):
    i = get_index(user_id, scope)
    set_index(user_id, scope, i + 1)
    return i + 1
