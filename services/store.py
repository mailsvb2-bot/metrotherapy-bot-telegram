from __future__ import annotations
import logging


from dataclasses import dataclass
from datetime import datetime
from core.time_utils import utc_now
from pathlib import Path

from core.paths import LOGS_DIR
from services.db import db, tx
from services.subscription import is_active, get_scope
from services.events import log_event

LOG_FILE = LOGS_DIR / "store.log"


def _log(msg: str):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{utc_now().isoformat()} {msg}\n")
    except (OSError, TypeError, ValueError):
        logging.getLogger(__name__).exception("store log write failed")


@dataclass
class ScheduledUser:
    user_id: int
    work_time: str | None
    home_time: str | None
    last_work_date: str | None
    last_home_date: str | None


class Store:
    def ensure_user(self, user_id: int, username: str | None = None, first_name: str | None = None):
        # ВАЖНО: все действия по пользователю делаем в одной транзакции.
        # Это снижает риск 'database is locked' и делает операцию идемпотентной.
        with db() as conn:
            with tx(conn):
                row = conn.execute("SELECT user_id FROM users WHERE user_id=?", (int(user_id),)).fetchone()
                if not row:
                    conn.execute(
                        "INSERT INTO users(user_id, joined_at, username, first_name) VALUES(?,?,?,?)",
                        (int(user_id), utc_now().replace(microsecond=0).isoformat(), username, first_name),
                    )
                    log_event(user_id, "user_joined", {"username": username, "first_name": first_name}, conn=conn)
                    _log(f"user_joined {user_id} @{username or ''}")
                else:
                    conn.execute(
                        "UPDATE users SET username=COALESCE(?, username), first_name=COALESCE(?, first_name) WHERE user_id=?",
                        (username, first_name, int(user_id)),
                    )

    def set_time(self, user_id: int, kind: str, hhmm: str):
        col = "work_time" if kind == "work" else "home_time"
        with db() as conn:
            with tx(conn):
                conn.execute(f"UPDATE users SET {col}=? WHERE user_id=?", (hhmm, int(user_id)))
        log_event(user_id, "time_set", {"kind": kind, "time": hhmm})
        _log(f"time_set {user_id} {kind} {hhmm}")

    def get_index(self, user_id: int, kind: str) -> int:
        col = "work_index" if kind == "work" else "home_index"
        with db() as conn:
            row = conn.execute(f"SELECT {col} v FROM users WHERE user_id=?", (int(user_id),)).fetchone()
        return int(row["v"]) if row and row["v"] is not None else (1 if kind == "work" else 2)

    def increment_index(self, user_id: int, kind: str):
        col = "work_index" if kind == "work" else "home_index"
        with db() as conn:
            with tx(conn):
                conn.execute(f"UPDATE users SET {col}={col}+2 WHERE user_id=?", (int(user_id),))
        _log(f"index_inc {user_id} {kind}")

    def mark_sent_today(self, user_id: int, kind: str, day_iso: str):
        col = "last_work_date" if kind == "work" else "last_home_date"
        with db() as conn:
            with tx(conn):
                conn.execute(f"UPDATE users SET {col}=? WHERE user_id=?", (day_iso, int(user_id)))
        log_event(user_id, "audio_sent", {"kind": kind, "day": day_iso})
        _log(f"audio_sent {user_id} {kind} {day_iso}")

    def list_scheduled_users(self) -> list[ScheduledUser]:
        with db() as conn:
            rows = conn.execute(
                "SELECT user_id, work_time, home_time, last_work_date, last_home_date FROM users"
            ).fetchall()
        return [
            ScheduledUser(
                user_id=int(r["user_id"]),
                work_time=r["work_time"],
                home_time=r["home_time"],
                last_work_date=r["last_work_date"],
                last_home_date=r["last_home_date"],
            )
            for r in rows
        ]

    def is_sub_active(self, user_id: int) -> bool:
        return is_active(user_id)

    def get_sub_scope(self, user_id: int) -> str | None:
        return get_scope(user_id)

    def count_users(self) -> int:
        with db() as conn:
            row = conn.execute("SELECT COUNT(*) c FROM users").fetchone()
        return int(row["c"])

    def users_missing_times(self) -> dict[str, int]:
        with db() as conn:
            w = conn.execute("SELECT COUNT(*) c FROM users WHERE work_time IS NULL OR work_time='' ").fetchone()["c"]
            h = conn.execute("SELECT COUNT(*) c FROM users WHERE home_time IS NULL OR home_time='' ").fetchone()["c"]
        return {"missing_work": int(w), "missing_home": int(h)}

    def demo_uses(self, user_id: int) -> int:
        with db() as conn:
            row = conn.execute("SELECT demo_uses v FROM users WHERE user_id=?", (int(user_id),)).fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    def inc_demo_uses(self, user_id: int):
        with db() as conn:
            with tx(conn):
                conn.execute("UPDATE users SET demo_uses=COALESCE(demo_uses,0)+1 WHERE user_id=?", (int(user_id),))
        _log(f"demo_use {user_id}")


store = Store()
