from __future__ import annotations

"""Synthetic user-journey E2E probe.

This probe does not send Telegram messages and does not contact YooKassa. It
executes the local production-critical path end-to-end with a reserved synthetic
user id:

1. demo mood session: pre-score -> demo sent/ack -> post-score;
2. synthetic payment webhook replay: token grant + premium entitlement side effects;
3. paid scheduled practice: pre-score marker -> token reserve/consume -> audio marker -> post-score;
4. cleanup and probe-ledger evidence.
"""

import argparse
import json
import os
import shlex
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = Path("/etc/metrotherapy/metrotherapy.env")
DEFAULT_SYNTHETIC_USER_ID = -910_000_501
DEFAULT_PACKAGE_ID = "practice_personal_month"
PROBE_TYPE = "synthetic_user_journey_e2e_probe"
PROVIDER = "yookassa"
PROBE_SOURCE = "synthetic_user_journey_e2e"


@dataclass(frozen=True)
class UserJourneyProbeResult:
    ok: bool
    run_id: str
    user_id: int
    payment_id: str
    demo_session_id: int
    paid_session_id: int
    wallet_delta_after_payment: int
    available_tokens_after_paid_audio: int
    used_tokens_after_paid_audio: int
    entitlement_rows_delta: int
    outbox_rows_delta: int
    consultation_rows_delta: int
    demo_ack_ok: bool
    paid_reservation_reason: str
    cleanup_status: str
    rows_touched: int
    problems: list[str]


def _load_env_file(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    env_path = Path(path)
    if not env_path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        try:
            parts = shlex.split(value, posix=True)
            loaded[key] = parts[0] if len(parts) == 1 else value
        except ValueError:
            loaded[key] = value.strip('"').strip("'")
    return loaded


def _apply_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ.setdefault(str(key), str(value))


def _imports():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from core.time_utils import utcnow_iso
    from services.audio_anchor import pick_for_slot
    from services.db import db, mark_delivery_once, was_delivered
    from services.demo_analytics import record_demo_ack, record_demo_sent
    from services.idempotency_keys import for_pre_score, for_session
    from services.mood import create_session, get_session, mark_audio_sent, set_post, set_pre
    from services.payments.reconciliation import record_yookassa_webhook
    from services.practice_token_contract import package_by_id
    from services.practice_tokens import check_and_reserve_for_audio, finalize_audio_access, get_wallet
    from services.probe_ledger import assert_synthetic_user_id, finish_probe_run, start_probe_run
    from services.schema import init_db

    return {
        "utcnow_iso": utcnow_iso,
        "pick_for_slot": pick_for_slot,
        "db": db,
        "mark_delivery_once": mark_delivery_once,
        "was_delivered": was_delivered,
        "record_demo_ack": record_demo_ack,
        "record_demo_sent": record_demo_sent,
        "for_pre_score": for_pre_score,
        "for_session": for_session,
        "create_session": create_session,
        "get_session": get_session,
        "mark_audio_sent": mark_audio_sent,
        "set_post": set_post,
        "set_pre": set_pre,
        "record_yookassa_webhook": record_yookassa_webhook,
        "package_by_id": package_by_id,
        "check_and_reserve_for_audio": check_and_reserve_for_audio,
        "finalize_audio_access": finalize_audio_access,
        "get_wallet": get_wallet,
        "assert_synthetic_user_id": assert_synthetic_user_id,
        "finish_probe_run": finish_probe_run,
        "start_probe_run": start_probe_run,
        "init_db": init_db,
    }


def _delete_with_count(conn, sql: str, params: tuple[Any, ...]) -> int:
    cur = conn.execute(sql, params)
    return max(int(getattr(cur, "rowcount", 0) or 0), 0)


def _cleanup_probe_rows(*, db, assert_synthetic_user_id, user_id: int, payment_id: str | None = None) -> int:
    assert_synthetic_user_id(int(user_id))
    touched = 0
    with db() as conn:
        if payment_id:
            touched += _delete_with_count(conn, "DELETE FROM consultation_requests WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id))
            touched += _delete_with_count(conn, "DELETE FROM premium_delivery_outbox WHERE idempotency_key LIKE ?", (f"%{payment_id}%",))
            touched += _delete_with_count(conn, "DELETE FROM premium_entitlements WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id))
            touched += _delete_with_count(conn, "DELETE FROM payment_token_grants WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id))
            touched += _delete_with_count(conn, "DELETE FROM payments WHERE provider_charge_id=? OR telegram_charge_id=?", (payment_id, f"yookassa:{payment_id}"))
            touched += _delete_with_count(conn, "DELETE FROM practice_ledger WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id))
        for sql, params in (
            ("DELETE FROM practice_reservations WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM practice_ledger WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM practice_wallets WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM user_practice_preferences WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM premium_delivery_outbox WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM premium_entitlements WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM consultation_requests WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM demo_events WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM mood_sessions WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM idempotency WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM events WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM users WHERE user_id=?", (int(user_id),)),
        ):
            try:
                touched += _delete_with_count(conn, sql, params)
            except Exception:
                # Some old schemas may not have all optional tables/columns in local tests.
                continue
    return touched


def _row_count(conn, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _wallet_tokens(get_wallet, user_id: int) -> tuple[int, int, int]:
    wallet = get_wallet(int(user_id))
    return int(wallet.available_tokens), int(wallet.reserved_tokens), int(wallet.used_tokens)


def _payment_payload(*, payment_id: str, user_id: int, package_id: str, amount: str) -> dict[str, Any]:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": amount, "currency": "RUB"},
            "metadata": {
                "project": "metrotherapy",
                "user_id": str(int(user_id)),
                "external_user_id": str(int(user_id)),
                "source": "telegram",
                "kind": "tokens",
                "package_id": package_id,
            },
        },
    }


def run_probe(*, user_id: int = DEFAULT_SYNTHETIC_USER_ID, keep_artifacts: bool = False) -> UserJourneyProbeResult:
    deps = _imports()
    deps["assert_synthetic_user_id"](int(user_id))
    deps["init_db"]()

    package = deps["package_by_id"](DEFAULT_PACKAGE_ID)
    payment_id = f"probe-e2e-{uuid.uuid4().hex[:12]}"
    run_id = uuid.uuid4().hex
    started = deps["utcnow_iso"]()
    deps["start_probe_run"](
        probe_type=PROBE_TYPE,
        user_id=int(user_id),
        run_id=run_id,
        evidence={"payment_id": payment_id, "package_id": DEFAULT_PACKAGE_ID, "started": started},
    )
    rows_touched = 0
    problems: list[str] = []
    demo_session_id = 0
    paid_session_id = 0
    demo_ack_ok = False
    paid_reservation_reason = "not_started"
    entitlement_rows_delta = 0
    outbox_rows_delta = 0
    consultation_rows_delta = 0
    wallet_delta_after_payment = 0
    available_after_paid_audio = 0
    used_after_paid_audio = 0
    cleanup_status = "not_started"

    try:
        rows_touched += _cleanup_probe_rows(
            db=deps["db"],
            assert_synthetic_user_id=deps["assert_synthetic_user_id"],
            user_id=int(user_id),
            payment_id=payment_id,
        )
        with deps["db"]() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users(user_id, work_time, home_time) VALUES(?,?,?)",
                (int(user_id), "08:30", "19:30"),
            )
        rows_touched += 1

        local_day = datetime.now(timezone.utc).date().isoformat()
        demo_audio = deps["pick_for_slot"]("morning", 0)
        if demo_audio is None:
            problems.append("no_demo_anchor")
        demo_anchor = int(getattr(demo_audio, "anchor", 0) or 0)
        demo_session_id = deps["create_session"](
            int(user_id),
            kind="work",
            source="demo",
            day=local_day,
            slot="morning",
            scheduled_at=f"e2e-demo:{run_id}",
            anchor_id=demo_anchor or None,
        )
        if not deps["set_pre"](demo_session_id, 1):
            problems.append("demo_pre_score_failed")
        demo_scheduled_at = deps["for_session"](demo_session_id)
        deps["mark_delivery_once"](int(user_id), "demo", "audio", demo_scheduled_at)
        deps["mark_audio_sent"](demo_session_id)
        if not deps["set_post"](demo_session_id, 3):
            problems.append("demo_post_score_failed")
        sent_at = deps["utcnow_iso"]()
        deps["record_demo_sent"](int(user_id), "work", int(demo_session_id), sent_at, 1200)
        demo_ack_ok = deps["record_demo_ack"](int(user_id), "work", int(demo_session_id), deps["utcnow_iso"]())
        if not demo_ack_ok:
            problems.append("demo_ack_failed")
        demo_session = deps["get_session"](demo_session_id)
        if demo_session is None or int(demo_session.audio_sent) != 1:
            problems.append("demo_audio_not_marked_sent")

        before_available, _before_reserved, before_used = _wallet_tokens(deps["get_wallet"], int(user_id))
        before_counts: dict[str, int]
        with deps["db"]() as conn:
            before_counts = {
                "entitlements": _row_count(conn, "SELECT COUNT(*) FROM premium_entitlements WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id)),
                "outbox": _row_count(conn, "SELECT COUNT(*) FROM premium_delivery_outbox WHERE idempotency_key LIKE ?", (f"%{payment_id}%",)),
                "consultation": _row_count(conn, "SELECT COUNT(*) FROM consultation_requests WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id)),
            }
        payload = _payment_payload(
            payment_id=payment_id,
            user_id=int(user_id),
            package_id=DEFAULT_PACKAGE_ID,
            amount=f"{package.price_rub}.00",
        )
        first_payment = deps["record_yookassa_webhook"](payload)
        second_payment = deps["record_yookassa_webhook"](payload)
        if not first_payment.ok or not first_payment.inserted:
            problems.append("first_payment_not_applied")
        if not second_payment.ok or second_payment.inserted:
            problems.append("payment_not_idempotent")
        after_available, _after_reserved, _after_used = _wallet_tokens(deps["get_wallet"], int(user_id))
        wallet_delta_after_payment = after_available - before_available
        if wallet_delta_after_payment != int(package.tokens):
            problems.append(f"wallet_delta_mismatch:{wallet_delta_after_payment}")
        with deps["db"]() as conn:
            after_counts = {
                "entitlements": _row_count(conn, "SELECT COUNT(*) FROM premium_entitlements WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id)),
                "outbox": _row_count(conn, "SELECT COUNT(*) FROM premium_delivery_outbox WHERE idempotency_key LIKE ?", (f"%{payment_id}%",)),
                "consultation": _row_count(conn, "SELECT COUNT(*) FROM consultation_requests WHERE provider=? AND provider_payment_id=?", (PROVIDER, payment_id)),
            }
        entitlement_rows_delta = after_counts["entitlements"] - before_counts["entitlements"]
        outbox_rows_delta = after_counts["outbox"] - before_counts["outbox"]
        consultation_rows_delta = after_counts["consultation"] - before_counts["consultation"]
        if entitlement_rows_delta <= 0:
            problems.append("missing_entitlement")
        if outbox_rows_delta <= 0:
            problems.append("missing_outbox")
        if consultation_rows_delta <= 0:
            problems.append("missing_consultation")

        paid_audio = deps["pick_for_slot"]("evening", 0)
        if paid_audio is None:
            problems.append("no_paid_anchor")
        paid_anchor = int(getattr(paid_audio, "anchor", 0) or 0)
        paid_scheduled_at = deps["for_pre_score"](int(user_id), local_day, "evening")
        deps["mark_delivery_once"](int(user_id), "home", "pre_score", paid_scheduled_at)
        if not deps["was_delivered"](int(user_id), "home", "pre_score", paid_scheduled_at):
            problems.append("paid_pre_score_marker_missing")
        paid_session_id = deps["create_session"](
            int(user_id),
            kind="home",
            source=PROBE_SOURCE,
            day=local_day,
            slot="evening",
            scheduled_at=paid_scheduled_at,
            anchor_id=paid_anchor or None,
        )
        if not deps["set_pre"](paid_session_id, 2):
            problems.append("paid_pre_score_failed")
        decision = deps["check_and_reserve_for_audio"](
            int(user_id),
            is_demo=False,
            session_id=paid_session_id,
            audio_anchor=paid_anchor or None,
        )
        paid_reservation_reason = str(decision.reason)
        if not decision.allowed or not decision.reservation_id:
            problems.append(f"paid_audio_not_reserved:{decision.reason}")
        deps["finalize_audio_access"](decision, delivered=True)
        deps["mark_delivery_once"](int(user_id), "home", "audio", deps["for_session"](paid_session_id))
        deps["mark_audio_sent"](paid_session_id)
        if not deps["set_post"](paid_session_id, 5):
            problems.append("paid_post_score_failed")
        paid_session = deps["get_session"](paid_session_id)
        if paid_session is None or int(paid_session.audio_sent) != 1:
            problems.append("paid_audio_not_marked_sent")
        available_after_paid_audio, reserved_after_paid_audio, used_tokens_after_paid_audio = _wallet_tokens(
            deps["get_wallet"], int(user_id)
        )
        if reserved_after_paid_audio != 0:
            problems.append(f"reservation_not_consumed:{reserved_after_paid_audio}")
        if used_tokens_after_paid_audio - before_used != 1:
            problems.append(f"used_tokens_delta_mismatch:{used_tokens_after_paid_audio - before_used}")

        cleanup_status = "kept"
        if not keep_artifacts:
            rows_touched += _cleanup_probe_rows(
                db=deps["db"],
                assert_synthetic_user_id=deps["assert_synthetic_user_id"],
                user_id=int(user_id),
                payment_id=payment_id,
            )
            cleanup_status = "clean"

        result = UserJourneyProbeResult(
            ok=not problems,
            run_id=run_id,
            user_id=int(user_id),
            payment_id=payment_id,
            demo_session_id=int(demo_session_id),
            paid_session_id=int(paid_session_id),
            wallet_delta_after_payment=int(wallet_delta_after_payment),
            available_tokens_after_paid_audio=int(available_after_paid_audio),
            used_tokens_after_paid_audio=int(used_tokens_after_paid_audio),
            entitlement_rows_delta=int(entitlement_rows_delta),
            outbox_rows_delta=int(outbox_rows_delta),
            consultation_rows_delta=int(consultation_rows_delta),
            demo_ack_ok=bool(demo_ack_ok),
            paid_reservation_reason=paid_reservation_reason,
            cleanup_status=cleanup_status,
            rows_touched=int(rows_touched),
            problems=problems,
        )
        deps["finish_probe_run"](
            run_id=run_id,
            status="ok" if result.ok else "failed",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            error=";".join(problems) or None,
            evidence=asdict(result),
        )
        return result
    except Exception as exc:
        problems.append(f"unexpected:{type(exc).__name__}:{exc}")
        if cleanup_status == "not_started" and not keep_artifacts:
            try:
                rows_touched += _cleanup_probe_rows(
                    db=deps["db"],
                    assert_synthetic_user_id=deps["assert_synthetic_user_id"],
                    user_id=int(user_id),
                    payment_id=payment_id,
                )
                cleanup_status = "clean"
            except Exception:
                cleanup_status = "failed"
        deps["finish_probe_run"](
            run_id=run_id,
            status="failed",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            error=";".join(problems),
            evidence={"payment_id": payment_id, "problems": problems},
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Run synthetic demo-payment-scheduled-audio user journey probe")
    parser.add_argument("--env-file", default=os.getenv("METROTHERAPY_ENV_FILE", str(DEFAULT_ENV_FILE)))
    parser.add_argument("--user-id", type=int, default=DEFAULT_SYNTHETIC_USER_ID)
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    _apply_env(_load_env_file(args.env_file))
    result = run_probe(user_id=int(args.user_id), keep_artifacts=bool(args.keep_artifacts))
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "USER_JOURNEY_E2E_OK "
            f"user_id={result.user_id} run_id={result.run_id} cleanup={result.cleanup_status} "
            f"rows_touched={result.rows_touched} wallet_delta={result.wallet_delta_after_payment} "
            f"used_tokens={result.used_tokens_after_paid_audio}"
        )
    return 0 if result.ok and result.cleanup_status in {"clean", "kept"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
