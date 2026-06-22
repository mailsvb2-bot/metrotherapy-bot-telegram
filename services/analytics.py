from datetime import datetime
from core.time_utils import utc_now
from services.db import db

def log(event: str, user_id: int):
    with db() as conn:
        conn.execute(
            "INSERT INTO events(user_id, event, ts) VALUES (?, ?, ?)",
            (user_id, event, utc_now().isoformat())
        )
