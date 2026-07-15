from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one target, found {count}")
    print(f"PATCH_OK {label}")
    return text.replace(old, new, 1)


def replace_span(text: str, start_marker: str, end_marker: str, replacement: str, *, label: str) -> str:
    start_count = text.count(start_marker)
    if start_count != 1:
        raise RuntimeError(f"{label}: start marker count={start_count}")
    start = text.index(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        raise RuntimeError(f"{label}: end marker missing")
    print(f"PATCH_OK {label}")
    return text[:start] + replacement + text[end:]


def patch_db_core() -> None:
    path = "services/db/core.py"
    text = read(path)
    text = replace_span(
        text,
        "def _raw_pg_connection_is_usable(conn: Any) -> bool:\n",
        "\n\ndef _close_raw_pg_connection",
        '''def _raw_pg_connection_is_usable(conn: Any) -> bool:
    """Prove that a cached Postgres connection is alive before reuse.

    PostgreSQL, a proxy or the network can sever an idle socket while psycopg
    still reports ``closed == False``. A lightweight pre-ping plus rollback
    prevents the next business operation from inheriting a dead or ping-opened
    transaction.
    """
    try:
        if bool(getattr(conn, "closed", False)):
            return False
        conn.execute("SELECT 1")
        conn.rollback()
        return True
    except Exception:  # validator: allow-wide-except
        return False
''',
        label="postgres reusable connection pre-ping",
    )
    text = replace_span(
        text,
        "def execute(sql: str, params: tuple[Any, ...] = ()):",
        "\n\n@contextmanager\ndef tx",
        '''def execute(
    sql: str,
    params: Sequence[Any] = (),
    *,
    fetchone: bool = False,
    fetchall: bool = False,
):
    """Execute one statement and materialize results before close."""
    if fetchone and fetchall:
        raise ValueError("execute accepts only one of fetchone/fetchall")
    with db() as conn:
        cursor = conn.execute(sql, tuple(params))
        if fetchone:
            return cursor.fetchone()
        if fetchall:
            return cursor.fetchall()
        if getattr(cursor, "description", None) is not None:
            return cursor.fetchall()
        try:
            return int(getattr(cursor, "rowcount", 0) or 0)
        except (TypeError, ValueError):
            return 0
''',
        label="core execute materialization",
    )
    text = replace_span(
        text,
        "def was_delivered(user_id: int, key: str) -> bool:\n",
        "__END_OF_FILE__",
        '''def _delivery_key(*parts: Any) -> str:
    cleaned = [str(part).strip() for part in parts if str(part).strip()]
    if not cleaned:
        raise ValueError("delivery idempotency key must not be empty")
    return ":".join(cleaned)


def _is_deferred_engine_job_marker(*parts: Any) -> bool:
    return len(parts) >= 3 and str(parts[0]).strip() == "job"


def was_delivered(user_id: int, *key_parts: Any) -> bool:
    key = _delivery_key(*key_parts)
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
            (int(user_id), key),
        ).fetchone()
        return bool(row)


def mark_delivery_once(user_id: int, *key_parts: Any) -> bool:
    key = _delivery_key(*key_parts)
    if _is_deferred_engine_job_marker(*key_parts):
        return True
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO idempotency(user_id, key, created_at) VALUES(?,?,?)",
            (int(user_id), key, int(time.time())),
        )
        row = conn.execute("SELECT changes() AS c").fetchone()
        return int(row["c"] if hasattr(row, "keys") else row[0]) == 1


def unmark_delivery(user_id: int, *key_parts: Any) -> None:
    key = _delivery_key(*key_parts)
    with db() as conn:
        conn.execute("DELETE FROM idempotency WHERE user_id=? AND key=?", (int(user_id), key))
''',
        label="canonical core idempotency API",
    ) if False else text
    # The idempotency helpers are the final block in this module.
    start_marker = "def was_delivered(user_id: int, key: str) -> bool:\n"
    if text.count(start_marker) != 1:
        raise RuntimeError(f"canonical core idempotency API: start marker count={text.count(start_marker)}")
    text = text[: text.index(start_marker)] + '''def _delivery_key(*parts: Any) -> str:
    cleaned = [str(part).strip() for part in parts if str(part).strip()]
    if not cleaned:
        raise ValueError("delivery idempotency key must not be empty")
    return ":".join(cleaned)


def _is_deferred_engine_job_marker(*parts: Any) -> bool:
    return len(parts) >= 3 and str(parts[0]).strip() == "job"


def was_delivered(user_id: int, *key_parts: Any) -> bool:
    key = _delivery_key(*key_parts)
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
            (int(user_id), key),
        ).fetchone()
        return bool(row)


def mark_delivery_once(user_id: int, *key_parts: Any) -> bool:
    key = _delivery_key(*key_parts)
    if _is_deferred_engine_job_marker(*key_parts):
        return True
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO idempotency(user_id, key, created_at) VALUES(?,?,?)",
            (int(user_id), key, int(time.time())),
        )
        row = conn.execute("SELECT changes() AS c").fetchone()
        return int(row["c"] if hasattr(row, "keys") else row[0]) == 1


def unmark_delivery(user_id: int, *key_parts: Any) -> None:
    key = _delivery_key(*key_parts)
    with db() as conn:
        conn.execute("DELETE FROM idempotency WHERE user_id=? AND key=?", (int(user_id), key))
'''
    print("PATCH_OK canonical core idempotency API")
    write(path, text)

    write(
        "services/db/__init__.py",
        '''from __future__ import annotations
"""Backward-compatible public database package.

All runtime helpers are implemented once in :mod:`services.db.core` and
re-exported here. The package remains callable for legacy
``from services import db`` call sites.
"""

from services.db.core import (
    DB_PATH,
    PROJECT_ROOT,
    db,
    execute,
    get_connection,
    get_db,
    get_db_ro,
    mark_delivery_once,
    tx,
    unmark_delivery,
    was_delivered,
    write,
)

from services.db import schema

import sys as _sys
import types as _types


class _CallableDbPackage(_types.ModuleType):
    def __call__(self, *args, **kwargs):
        return db(*args, **kwargs)


_sys.modules[__name__].__class__ = _CallableDbPackage
''',
    )
    print("PATCH_OK package DB re-export")


def patch_refunds() -> None:
    path = "services/payments/telegram_stars_refunds.py"
    text = read(path)
    text = replace_span(
        text,
        "def _premium_refund_problem(*, user_id: int, charge_id: str) -> str:\n",
        "\n\ndef _payment_record",
        '''def _premium_refund_problem_in_conn(conn: Any, *, user_id: int, charge_id: str) -> str:
    prefix = _delivery_pattern(charge_id)
    delivered = conn.execute(
        """
        SELECT status
        FROM premium_delivery_outbox
        WHERE user_id=? AND idempotency_key LIKE ? ESCAPE '!'
          AND status NOT IN ('pending', 'refund_pending', 'cancelled')
        LIMIT 1
        """.strip(),
        (int(user_id), prefix),
    ).fetchone()
    consultation = conn.execute(
        """
        SELECT status
        FROM consultation_requests
        WHERE user_id=? AND provider=? AND provider_payment_id=?
          AND status NOT IN ('new', 'refund_pending', 'cancelled')
        LIMIT 1
        """.strip(),
        (int(user_id), STARS_PROVIDER, charge_id),
    ).fetchone()
    if delivered:
        return "premium_content_already_delivered"
    if consultation:
        return "consultation_already_in_progress"
    return ""


def _premium_refund_problem(*, user_id: int, charge_id: str) -> str:
    with db() as conn:
        return _premium_refund_problem_in_conn(conn, user_id=user_id, charge_id=charge_id)


def _claim_refundable_side_effects(conn: Any, *, user_id: int, charge_id: str) -> str:
    """Freeze pending fulfilment and recheck after acquiring DB row locks."""
    prefix = _delivery_pattern(charge_id)
    conn.execute(
        """
        UPDATE premium_entitlements
        SET status='refund_pending', updated_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND provider=? AND provider_payment_id=? AND status='active'
        """.strip(),
        (int(user_id), STARS_PROVIDER, charge_id),
    )
    conn.execute(
        """
        UPDATE premium_delivery_outbox
        SET status='refund_pending', updated_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND idempotency_key LIKE ? ESCAPE '!' AND status='pending'
        """.strip(),
        (int(user_id), prefix),
    )
    conn.execute(
        """
        UPDATE consultation_requests
        SET status='refund_pending', updated_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND provider=? AND provider_payment_id=? AND status='new'
        """.strip(),
        (int(user_id), STARS_PROVIDER, charge_id),
    )
    return _premium_refund_problem_in_conn(conn, user_id=user_id, charge_id=charge_id)
''',
        label="refund in-transaction side-effect claim",
    )
    prepare_start = text.index("def prepare_stars_refund(")
    prepare_end = text.index("\ndef cancel_prepared_stars_refund", prepare_start)
    prepare = text[prepare_start:prepare_end]
    prepare = replace_once(
        prepare,
        "            if plan.tokens:\n",
        '''            side_effect_problem = _claim_refundable_side_effects(
                conn,
                user_id=plan.beneficiary_user_id,
                charge_id=charge_id,
            )
            if side_effect_problem:
                raise StarsRefundError(side_effect_problem)

            if plan.tokens:
''',
        label="refund prepare atomic recheck",
    )
    side_start = prepare.index(
        "            conn.execute(\n                \"\"\"\n                UPDATE premium_entitlements"
    )
    side_end = prepare.index("            if plan.gift_token:", side_start)
    prepare = prepare[:side_start] + prepare[side_end:]
    print("PATCH_OK remove duplicate refund side-effect updates")
    text = text[:prepare_start] + prepare + text[prepare_end:]
    write(path, text)


def patch_yookassa_log() -> None:
    path = "services/payments/yookassa_checkout.py"
    text = read(path)
    text = replace_once(
        text,
        '''    if not confirmation_url:
        log.error("YooKassa payment response without confirmation_url: %s", data)
        raise YooKassaCheckoutError("YooKassa response without confirmation_url")
''',
        '''    if not confirmation_url:
        log.error(
            "YooKassa payment response without confirmation_url: %s",
            _provider_error_body_for_log(raw),
        )
        raise YooKassaCheckoutError("YooKassa response without confirmation_url")
''',
        label="YooKassa malformed response redaction",
    )
    write(path, text)


def patch_app() -> None:
    path = "app.py"
    text = read(path)
    text = replace_once(
        text,
        "log = logging.getLogger(__name__)\n\nasync def _safe_answer_callback",
        '''log = logging.getLogger(__name__)


def _runtime_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        log.warning("Out-of-range %s=%r; using %s", name, raw, default)
        return default
    return value


def _runtime_float(
    name: str,
    default: float,
    *,
    fallback_name: str = "",
    minimum: float | None = None,
) -> float:
    raw = os.getenv(name)
    if raw in (None, "") and fallback_name:
        raw = os.getenv(fallback_name)
    raw = str(default) if raw in (None, "") else str(raw).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        log.warning("Out-of-range %s=%r; using %s", name, raw, default)
        return default
    return value


async def _rollback_partial_startup(
    *,
    webhook_runtime,
    health_runtime,
    scheduler_started: bool,
    db_writer_started: bool,
) -> None:
    """Best-effort reverse-order rollback without masking startup failure."""
    if health_runtime is not None:
        try:
            await health_runtime.stop()
        except Exception:  # validator: allow-wide-except
            log.exception("Partial-startup health rollback failed")
    if webhook_runtime is not None:
        try:
            await webhook_runtime.stop()
        except Exception:  # validator: allow-wide-except
            log.exception("Partial-startup webhook rollback failed")
    if scheduler_started:
        try:
            await stop_scheduler()
        except Exception:  # validator: allow-wide-except
            log.exception("Partial-startup scheduler rollback failed")
    if db_writer_started:
        try:
            await stop_db_writer(drain=False)
        except Exception:  # validator: allow-wide-except
            log.exception("Partial-startup DB writer rollback failed")


async def _safe_answer_callback''',
        label="app runtime parsers and rollback helper",
    )
    text = replace_once(
        text,
        '''async def create_application():
    webhook_runtime = None
    health_runtime = None

    async def _on_startup(bot: Bot):
''',
        '''async def create_application():
    webhook_runtime = None
    health_runtime = None
    scheduler_started = False
    db_writer_started = False

    async def _on_startup(bot: Bot):
        nonlocal webhook_runtime, health_runtime, scheduler_started, db_writer_started
''',
        label="startup ownership state",
    )
    start = text.index("        start_db_writer()\n")
    end = text.index("\n    async def _on_shutdown", start)
    startup_replacement = '''        try:
            start_db_writer()
            db_writer_started = True
            start_scheduler(bot)
            scheduler_started = True
            try:
                webhook_runtime = await start_messenger_webhook_runtime(bot=bot, dispatcher=dp)
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):  # validator: allow-wide-except
                webhook_runtime = None
                selected_transport = telegram_transport()
                log.exception('Messenger/Telegram webhook runtime failed to start')
                if selected_transport == 'webhook' or app_env == 'prod':
                    raise
                log.warning('Continuing without optional messenger webhook runtime in non-prod polling mode')

            try:
                health_runtime = await start_health_runtime()
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):  # validator: allow-wide-except
                health_runtime = None
                log.exception('Health runtime failed to start')
                if app_env == 'prod':
                    raise
                log.warning('Continuing without health endpoint in non-prod mode')

            try:
                await prewarm_audio_cache(bot)
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):  # validator: allow-wide-except
                log.exception("Prewarm audio cache failed")

            try:
                await prewarm_matplotlib_cache()
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):  # validator: allow-wide-except
                log.exception("Prewarm matplotlib cache failed")
        except BaseException:  # validator: allow-wide-except
            await _rollback_partial_startup(
                webhook_runtime=webhook_runtime,
                health_runtime=health_runtime,
                scheduler_started=scheduler_started,
                db_writer_started=db_writer_started,
            )
            webhook_runtime = None
            health_runtime = None
            scheduler_started = False
            db_writer_started = False
            raise
'''
    text = text[:start] + startup_replacement + text[end:]
    print("PATCH_OK partial startup transaction")
    shutdown_start = text.index("    async def _on_shutdown(bot: Bot):\n")
    shutdown_end = text.index("\n    token =", shutdown_start)
    shutdown_replacement = '''    async def _on_shutdown(bot: Bot):
        nonlocal webhook_runtime, health_runtime, scheduler_started, db_writer_started
        if webhook_runtime is not None:
            await webhook_runtime.stop()
            webhook_runtime = None
        if health_runtime is not None:
            await health_runtime.stop()
            health_runtime = None
        if scheduler_started:
            await stop_scheduler()
            scheduler_started = False
        if db_writer_started:
            await stop_db_writer(drain=True)
            db_writer_started = False
        await tm.shutdown()
'''
    text = text[:shutdown_start] + shutdown_replacement + text[shutdown_end:]
    print("PATCH_OK shutdown ownership flags")
    text = replace_once(
        text,
        '    thr_ms = int(os.getenv("SLOW_HANDLER_MS", "700"))\n',
        '    thr_ms = _runtime_int("SLOW_HANDLER_MS", 700, minimum=1)\n',
        label="slow handler env parser",
    )
    text = replace_once(
        text,
        '''    callback_interval_sec = float(
        os.getenv("SOFT_CALLBACK_RATE_LIMIT_SEC", os.getenv("SOFT_RATE_LIMIT_SEC", "0.05")) or "0.05"
    )
    message_interval_sec = float(
        os.getenv("SOFT_MESSAGE_RATE_LIMIT_SEC", os.getenv("SOFT_RATE_LIMIT_SEC", "0.05")) or "0.05"
    )
''',
        '''    callback_interval_sec = _runtime_float(
        "SOFT_CALLBACK_RATE_LIMIT_SEC", 0.05, fallback_name="SOFT_RATE_LIMIT_SEC", minimum=0.0
    )
    message_interval_sec = _runtime_float(
        "SOFT_MESSAGE_RATE_LIMIT_SEC", 0.05, fallback_name="SOFT_RATE_LIMIT_SEC", minimum=0.0
    )
''',
        label="rate limit env parsers",
    )
    text = replace_once(
        text,
        "    max_retries = max(1, int(os.getenv('STARTUP_NETWORK_RETRIES', '5')))\n",
        '    max_retries = _runtime_int("STARTUP_NETWORK_RETRIES", 5, minimum=1)\n',
        label="startup retry env parser",
    )
    write(path, text)


def write_tests() -> None:
    write(
        "tests/test_runtime_hardening_closure.py",
        '''from __future__ import annotations

from types import SimpleNamespace

import pytest

import app
from services.db import core as db_core


def test_runtime_numeric_parsers_fail_closed(monkeypatch):
    monkeypatch.setenv("SLOW_HANDLER_MS", "broken")
    monkeypatch.setenv("SOFT_CALLBACK_RATE_LIMIT_SEC", "-5")
    assert app._runtime_int("SLOW_HANDLER_MS", 700, minimum=1) == 700
    assert app._runtime_float("SOFT_CALLBACK_RATE_LIMIT_SEC", 0.05, minimum=0.0) == 0.05


@pytest.mark.asyncio
async def test_partial_startup_rollback_runs_in_reverse_order(monkeypatch):
    calls: list[str] = []

    class Runtime:
        def __init__(self, name: str):
            self.name = name

        async def stop(self):
            calls.append(self.name)

    async def stop_scheduler():
        calls.append("scheduler")

    async def stop_db_writer(*, drain: bool):
        assert drain is False
        calls.append("db_writer")

    monkeypatch.setattr(app, "stop_scheduler", stop_scheduler)
    monkeypatch.setattr(app, "stop_db_writer", stop_db_writer)

    await app._rollback_partial_startup(
        webhook_runtime=Runtime("webhook"),
        health_runtime=Runtime("health"),
        scheduler_started=True,
        db_writer_started=True,
    )

    assert calls == ["health", "webhook", "scheduler", "db_writer"]


def test_cached_postgres_connection_is_pre_pinged_and_rolled_back():
    calls: list[str] = []

    class FakeConnection:
        closed = False

        def execute(self, sql: str):
            calls.append(sql)
            return SimpleNamespace()

        def rollback(self):
            calls.append("rollback")

    assert db_core._raw_pg_connection_is_usable(FakeConnection()) is True
    assert calls == ["SELECT 1", "rollback"]


def test_broken_cached_postgres_connection_is_rejected():
    class BrokenConnection:
        closed = False

        def execute(self, _sql: str):
            raise OSError("socket closed")

        def rollback(self):
            raise AssertionError("rollback must not be reached")

    assert db_core._raw_pg_connection_is_usable(BrokenConnection()) is False
''',
    )
    write(
        "tests/test_telegram_stars_refund_race.py",
        '''from __future__ import annotations

import uuid

import pytest

from services.db import db
from services.payments import telegram_stars, telegram_stars_refunds
from services.payments.telegram_stars import build_stars_payload, record_successful_stars_payment
from services.practice_token_contract import telegram_stars_price
from services.practice_tokens import get_wallet


def test_prepare_refund_rechecks_delivery_state_inside_transaction(monkeypatch):
    user_id = 783101
    charge_id = f"stars-refund-race-{uuid.uuid4().hex}"
    monkeypatch.setattr(telegram_stars, "log_event", lambda *args, **kwargs: None)
    payload = build_stars_payload(
        buyer_user_id=user_id,
        package_id="practice_antistress_60",
    )
    record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=telegram_stars_price("practice_antistress_60"),
        currency="XTR",
        telegram_charge_id=charge_id,
    )

    stale_plan = telegram_stars_refunds.preview_stars_refund(charge_id)
    assert stale_plan.refundable is True
    original_balance = get_wallet(user_id).available_tokens

    with db() as conn:
        conn.execute(
            "UPDATE premium_delivery_outbox SET status='processing' "
            "WHERE user_id=? AND idempotency_key LIKE ?",
            (user_id, f"premium_delivery:telegram_stars:{charge_id}:%"),
        )

    monkeypatch.setattr(
        telegram_stars_refunds,
        "preview_stars_refund",
        lambda _charge_id: stale_plan,
    )

    with pytest.raises(
        telegram_stars_refunds.StarsRefundError,
        match="premium_content_already_delivered",
    ):
        telegram_stars_refunds.prepare_stars_refund(charge_id, requested_by=900001)

    assert get_wallet(user_id).available_tokens == original_balance
    with db() as conn:
        refund = conn.execute(
            "SELECT status FROM telegram_stars_refunds WHERE telegram_charge_id=?",
            (charge_id,),
        ).fetchone()
    assert refund is None
''',
    )
    print("PATCH_OK regression tests")


def main() -> None:
    patch_db_core()
    patch_refunds()
    patch_yookassa_log()
    patch_app()
    write_tests()


if __name__ == "__main__":
    main()
