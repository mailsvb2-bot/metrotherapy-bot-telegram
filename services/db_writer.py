from __future__ import annotations


import asyncio
import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from core.paths import DB_PATH
from services.db.runtime import CONFIG, is_postgres_enabled

def _is_write_sql(sql: str) -> bool:
    """Best-effort check whether SQL mutates DB (needs commit)."""
    s = (sql or "").strip()
    if not s:
        return False
    # drop leading SQL comments
    while s.startswith("--"):
        nl = s.find("\n")
        if nl == -1:
            return False
        s = s[nl+1:].lstrip()
    first = re.match(r"^([A-Za-z_]+)", s)
    kw = (first.group(1).upper() if first else "")
    if kw == "WITH":
        m = re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE|SELECT)\b", s, flags=re.IGNORECASE)
        kw = (m.group(1).upper() if m else "WITH")
    return kw in {"INSERT","UPDATE","DELETE","REPLACE","CREATE","DROP","ALTER","VACUUM","PRAGMA","BEGIN","COMMIT"}
log = logging.getLogger(__name__)


@dataclass
class DbJob:
    sql: str
    params: Sequence[Any]
    many: bool
    fetchone: bool
    fetchall: bool
    fut: asyncio.Future


_queue: asyncio.Queue[DbJob] | None = None
_task: asyncio.Task | None = None


def _is_write(sql: str) -> bool:
    s = (sql or "").lstrip().upper()
    return s.startswith("INSERT") or s.startswith("UPDATE") or s.startswith("DELETE") or s.startswith(
        "REPLACE"
    ) or s.startswith("CREATE") or s.startswith("DROP") or s.startswith("ALTER")


def start_db_writer() -> None:
    """Start single-writer task for SQLite.

    In Postgres mode we intentionally skip the SQLite single-writer queue because
    the database already handles concurrent writes correctly.
    """
    global _queue, _task
    if is_postgres_enabled():
        _queue = None
        _task = None
        return
    if _task and not _task.done():
        return
    _queue = asyncio.Queue()
    try:
        from services.bg import tm

        _task = tm().create(_writer_loop())
    except (ImportError, AttributeError, RuntimeError):
        logging.getLogger(__name__).exception("db_writer: TaskManager unavailable, using create_task fallback")
        _task = asyncio.create_task(_writer_loop(), name="db-writer")
# asyncio.create_task(_writer_loop(), name="db-writer")


async def stop_db_writer(*, drain: bool = True) -> None:
    """Stop single-writer task.

    If drain=True, waits for queued jobs to finish.
    """
    global _queue, _task
    if not _task:
        return
    if drain and _queue:
        await _queue.join()
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    _queue = None


async def enqueue(
    sql: str,
    params: Sequence[Any] = (),
    *,
    many: bool = False,
    fetchone: bool = False,
    fetchall: bool = False,
):
    """Enqueue a DB statement to be executed by the single writer.

    Returns fetched row(s) for fetchone/fetchall, otherwise None.
    """
    if is_postgres_enabled():
        return await _execute_postgres_job(sql, params, many=many, fetchone=fetchone, fetchall=fetchall)
    if not _queue or not _task or _task.done():
        raise RuntimeError("DB writer is not running. Call start_db_writer() on startup.")

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    job = DbJob(sql=sql, params=tuple(params), many=bool(many), fetchone=bool(fetchone), fetchall=bool(fetchall), fut=fut)
    await _queue.put(job)
    return await fut


async def enqueue_many(sql: str, seq_of_params: Iterable[Sequence[Any]]):
    return await enqueue(sql, tuple(seq_of_params), many=True)




async def _execute_postgres_job(
    sql: str,
    params: Sequence[Any] = (),
    *,
    many: bool = False,
    fetchone: bool = False,
    fetchall: bool = False,
):
    def _run():
        from services.db.core import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            if many:
                cur.executemany(sql, params)  # type: ignore[arg-type]
                if _is_write_sql(sql):
                    conn.commit()
                return None
            cur.execute(sql, tuple(params))
            if fetchone:
                res = cur.fetchone()
                if _is_write_sql(sql):
                    conn.commit()
                return res
            if fetchall:
                res = cur.fetchall()
                if _is_write_sql(sql):
                    conn.commit()
                return res
            if _is_write_sql(sql):
                conn.commit()
            return None
        except Exception:  # validator: allow-wide-except
            try:
                conn.rollback()
            except Exception:  # validator: allow-wide-except
                logging.getLogger(__name__).exception("postgres job rollback failed")
            raise
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


async def _writer_loop() -> None:
    assert _queue is not None

    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        # Pragmas: one place, one writer.
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA temp_store=MEMORY;")
            conn.execute("PRAGMA busy_timeout=10000;")
            conn.execute("PRAGMA foreign_keys=ON;")
        except sqlite3.Error as e:
            log.warning("[DB] PRAGMA init failed: %s", e)

        while True:
            job: DbJob = await _queue.get()
            try:
                cur = conn.cursor()
                try:
                    is_write = _is_write_sql(job.sql)
                    if job.many:
                        # job.params is expected to be an iterable of params
                        cur.executemany(job.sql, job.params)  # type: ignore[arg-type]
                        if is_write:
                            conn.commit()
                        job.fut.set_result(None)
                    else:
                        cur.execute(job.sql, job.params)

                        # IMPORTANT: For statements with RETURNING (or any pending result set),
                        # we must fully consume/close the cursor BEFORE committing, otherwise
                        # SQLite can raise: "cannot commit transaction - SQL statements in progress".
                        if job.fetchone:
                            res = cur.fetchone()
                            # exhaust remaining rows to finalize the statement
                            try:
                                cur.fetchall()
                            except sqlite3.Error:
                                logging.getLogger(__name__).exception("Unhandled exception")
                            if is_write:
                                conn.commit()
                            job.fut.set_result(res)
                        elif job.fetchall:
                            res = cur.fetchall()
                            if is_write:
                                conn.commit()
                            job.fut.set_result(res)
                        else:
                            if is_write:
                                conn.commit()
                            job.fut.set_result(None)
                finally:
                    try:
                        cur.close()
                    except sqlite3.Error:
                        logging.getLogger(__name__).exception("Unhandled exception")
            except sqlite3.Error as e:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    logging.getLogger(__name__).exception("Unhandled exception")
                if not job.fut.done():
                    job.fut.set_exception(e)
            finally:
                _queue.task_done()
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            logging.getLogger(__name__).exception("Failed to close DB connection")
