from __future__ import annotations

"""Hermetic deep user-journey proof for payment, tokens, audio and messengers."""

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SYNTHETIC_USER_ID = -910_000_811


@dataclass(frozen=True)
class DeepJourneyProbeResult:
    ok: bool
    user_id: int
    checks: dict[str, bool]
    problems: list[str]
    detail: dict[str, Any]


class _SyntheticMaxSender:
    async def send_audio_file(
        self,
        external_user_id: str,
        file_path: Path,
        *,
        caption: str | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        del external_user_id, caption, kwargs
        if not Path(file_path).exists():
            raise OSError(f"synthetic MAX media missing: {file_path}")
        return SimpleNamespace(message_id="max-audio-1")

    async def send_text(
        self,
        external_user_id: str,
        text: str,
        **kwargs: Any,
    ) -> SimpleNamespace:
        del external_user_id, text, kwargs
        return SimpleNamespace(message_id="max-text-1")


def _smoke_bot_token() -> str:
    return "".join(("1234", "56789", ":", "ABCDE", "FGHIJ", "KLMNO", "PQRST", "UVWXY", "Zabcd", "efghi"))


def _prepare_env(db_path: Path) -> None:
    os.environ.update(
        {
            "APP_ENV": "test",
            "LOAD_DOTENV": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "METRO_DB_ENGINE": "sqlite",
            "METRO_DB_PATH": str(db_path),
            "DATABASE_URL": "",
            "BOT_TOKEN": _smoke_bot_token(),
            "ADMIN_IDS": "1",
            "TOKEN_ECONOMY_ENABLED": "1",
            "TOKEN_ENFORCEMENT_MODE": "hard",
            "TELEGRAM_TRANSPORT": "polling",
            "TELEGRAM_WEBHOOK_ENABLED": "0",
            "MESSENGER_WEBHOOK_ENABLED": "0",
            "VALIDATOR_SKIP_AUDIO": "1",
        }
    )


def _record(checks: dict[str, bool], problems: list[str], name: str, value: bool) -> None:
    checks[name] = bool(value)
    if not value:
        problems.append(name)


def _ensure_user(db: Callable[..., Any], user_id: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, work_time, home_time) VALUES(?,?,?)",
            (int(user_id), "08:30", "19:30"),
        )


def _count(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _concurrent_two(call_a: Callable[[], Any], call_b: Callable[[], Any]) -> tuple[Any, Any]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(call_a)
        future_b = executor.submit(call_b)
        return future_a.result(timeout=20), future_b.result(timeout=20)


def run_probe(*, user_id: int = DEFAULT_SYNTHETIC_USER_ID) -> DeepJourneyProbeResult:
    temp_dir = Path(tempfile.mkdtemp(prefix="metro_deep_journey_"))
    db_path = temp_dir / "deep-journey.db"
    checks: dict[str, bool] = {}
    problems: list[str] = []
    detail: dict[str, Any] = {}

    try:
        _prepare_env(db_path)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

        import services.fast_send_audio as fast_send_audio
        import services.mood_text_flow_core as mood_text_flow_core
        from services.audio_anchor import pick_for_slot
        from services.auto_audio_entitlement import eligible_user_ids
        from services.db import db
        from services.delivery_preferences import set_user_timezone
        from services.demo_analytics import demo_sent_kinds
        from services.funnel2 import should_skip_sales
        from services.messenger.audio_delivery import send_next_audio_to_user
        from services.messenger.audio_progress import confirm_pending_audio_delivery, get_progress_snapshot
        from services.messenger.outbound import SenderRegistry
        from services.messenger.text_ui_router import handle_incoming_text
        from services.mood import create_session, get_session
        from services.mood_text_flow import complete_post_score_and_send_next, complete_pre_score_and_send
        from services.practice_journey import start_or_resume_paid_practice
        from services.practice_tokens import get_wallet, grant_tokens, release_reservation, reserve_practice
        from services.schema import init_db
        from services.subscription import has_access, is_active

        init_db()
        uid = int(user_id)
        concurrency_uid = uid - 1
        race_uid = uid - 2
        demo_external_uid = abs(uid) + 1000
        for candidate in (uid, concurrency_uid, race_uid):
            _ensure_user(db, candidate)

        full_first = pick_for_slot("morning", 0)
        full_second = pick_for_slot("evening", 0)
        _record(checks, problems, "full_audio_catalog_available", full_first is not None and full_second is not None)
        if full_first is None or full_second is None:
            return DeepJourneyProbeResult(False, uid, checks, problems, detail)

        grant_tokens(
            concurrency_uid,
            package_id="practice_start_7",
            amount=2,
            provider="probe",
            provider_payment_id="deep-concurrency",
            source="deep_probe",
        )
        same_session = create_session(
            concurrency_uid,
            kind="work",
            source="settings",
            day="2026-07-12",
            slot="morning",
            scheduled_at=None,
            anchor_id=int(full_first.anchor),
        )
        same_a, same_b = _concurrent_two(
            lambda: reserve_practice(concurrency_uid, session_id=int(same_session), audio_anchor=int(full_first.anchor)),
            lambda: reserve_practice(concurrency_uid, session_id=int(same_session), audio_anchor=int(full_first.anchor)),
        )
        same_ids = {str(result[2]) for result in (same_a, same_b) if result[0] and result[2]}
        same_wallet = get_wallet(concurrency_uid)
        _record(checks, problems, "double_click_reuses_one_reservation", len(same_ids) == 1)
        _record(
            checks,
            problems,
            "double_click_reserves_one_token",
            int(same_wallet.available_tokens) == 1 and int(same_wallet.reserved_tokens) == 1,
        )
        if same_ids:
            release_reservation(next(iter(same_ids)), reason="deep_probe_reset")

        grant_tokens(
            race_uid,
            package_id="practice_start_7",
            amount=1,
            provider="probe",
            provider_payment_id="deep-last-token",
            source="deep_probe",
        )
        race_session_a = create_session(
            race_uid,
            kind="work",
            source="settings",
            day="2026-07-12",
            slot="morning",
            scheduled_at=None,
            anchor_id=int(full_first.anchor),
        )
        race_session_b = create_session(
            race_uid,
            kind="home",
            source="settings",
            day="2026-07-12",
            slot="evening",
            scheduled_at=None,
            anchor_id=int(full_second.anchor),
        )
        race_a, race_b = _concurrent_two(
            lambda: reserve_practice(race_uid, session_id=int(race_session_a), audio_anchor=int(full_first.anchor)),
            lambda: reserve_practice(race_uid, session_id=int(race_session_b), audio_anchor=int(full_second.anchor)),
        )
        race_successes = sum(1 for result in (race_a, race_b) if result[0])
        race_wallet = get_wallet(race_uid)
        _record(checks, problems, "last_token_cannot_be_overspent", race_successes == 1)
        _record(
            checks,
            problems,
            "last_token_wallet_invariant",
            int(race_wallet.available_tokens) == 0 and int(race_wallet.reserved_tokens) == 1,
        )

        grant_tokens(
            uid,
            package_id="practice_start_7",
            amount=3,
            provider="probe",
            provider_payment_id="deep-paid-route",
            source="deep_probe",
        )
        set_user_timezone(uid, "Europe/Amsterdam")
        start = start_or_resume_paid_practice(uid)
        _record(checks, problems, "paid_continue_starts_with_pre_score", start.ready_for_pre_score)
        if not start.ready_for_pre_score:
            return DeepJourneyProbeResult(False, uid, checks, problems, detail)

        async def fail_send(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise OSError("synthetic transport failure")

        async def success_send(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            return SimpleNamespace(message_id=777, voice=None, audio=None)

        fast_send_audio.send_audio_cached = fail_send
        wallet_before_fail = get_wallet(uid)
        send_failed = False
        try:
            asyncio.run(
                complete_pre_score_and_send(
                    uid,
                    platform="telegram",
                    score=2,
                    senders=SenderRegistry(),
                    telegram_bot=object(),
                    session_id=int(start.session_id),
                )
            )
        except OSError:
            send_failed = True
        wallet_after_fail = get_wallet(uid)
        failed_session = get_session(int(start.session_id))
        _record(checks, problems, "transport_failure_propagates", send_failed)
        _record(
            checks,
            problems,
            "transport_failure_releases_token",
            int(wallet_after_fail.available_tokens) == int(wallet_before_fail.available_tokens)
            and int(wallet_after_fail.reserved_tokens) == 0
            and int(wallet_after_fail.used_tokens) == int(wallet_before_fail.used_tokens),
        )
        _record(
            checks,
            problems,
            "failed_send_session_is_retryable",
            failed_session is not None and int(failed_session.audio_sent or 0) == 0,
        )

        fast_send_audio.send_audio_cached = success_send
        paid_result = asyncio.run(
            complete_pre_score_and_send(
                uid,
                platform="telegram",
                score=2,
                senders=SenderRegistry(),
                telegram_bot=object(),
                session_id=int(start.session_id),
            )
        )
        wallet_after_send = get_wallet(uid)
        _record(checks, problems, "paid_audio_retry_succeeds", paid_result.ok)
        _record(
            checks,
            problems,
            "successful_paid_audio_consumes_exactly_one",
            int(wallet_after_send.available_tokens) == 2
            and int(wallet_after_send.reserved_tokens) == 0
            and int(wallet_after_send.used_tokens) == 1,
        )

        duplicate_result = asyncio.run(
            complete_pre_score_and_send(
                uid,
                platform="telegram",
                score=2,
                senders=SenderRegistry(),
                telegram_bot=object(),
                session_id=int(start.session_id),
            )
        )
        wallet_after_duplicate = get_wallet(uid)
        _record(checks, problems, "stale_pre_callback_is_idempotent", duplicate_result.transport == "already_sent")
        _record(checks, problems, "stale_pre_callback_does_not_charge", wallet_after_duplicate == wallet_after_send)

        confirmed = confirm_pending_audio_delivery(uid, platform="telegram", sequence_key="full_series")
        _record(checks, problems, "done_confirms_paid_pending", confirmed is not None)
        post_result = asyncio.run(
            complete_post_score_and_send_next(
                uid,
                platform="telegram",
                score=5,
                senders=SenderRegistry(),
                telegram_bot=object(),
                session_id=int(start.session_id),
            )
        )
        duplicate_post = asyncio.run(
            complete_post_score_and_send_next(
                uid,
                platform="telegram",
                score=9,
                senders=SenderRegistry(),
                telegram_bot=object(),
                session_id=int(start.session_id),
            )
        )
        first_session = get_session(int(start.session_id))
        _record(
            checks,
            problems,
            "post_score_saved",
            post_result.ok and first_session is not None and int(first_session.post_score) == 5,
        )
        _record(
            checks,
            problems,
            "stale_post_callback_does_not_overwrite",
            duplicate_post.transport == "post_score_already_saved"
            and first_session is not None
            and int(first_session.post_score) == 5,
        )

        second_start = start_or_resume_paid_practice(uid)
        _record(checks, problems, "next_cycle_again_requires_pre_score", second_start.ready_for_pre_score)
        second_send = asyncio.run(
            complete_pre_score_and_send(
                uid,
                platform="telegram",
                score=1,
                senders=SenderRegistry(),
                telegram_bot=object(),
                session_id=int(second_start.session_id),
            )
        )
        wallet_before_replay = get_wallet(uid)
        replay_result = asyncio.run(
            send_next_audio_to_user(
                uid,
                senders=SenderRegistry(),
                telegram_bot=object(),
                target_platform="telegram",
                fallback="telegram",
            )
        )
        wallet_after_replay = get_wallet(uid)
        _record(checks, problems, "second_paid_audio_sent", second_send.ok)
        _record(checks, problems, "pending_audio_can_be_replayed", replay_result.item is not None)
        _record(checks, problems, "pending_replay_is_free", wallet_before_replay == wallet_after_replay)

        _record(checks, problems, "wallet_unlocks_legacy_active_check", is_active(uid))
        _record(checks, problems, "wallet_unlocks_morning_settings", has_access(uid, "morning"))
        _record(checks, problems, "wallet_unlocks_evening_settings", has_access(uid, "evening"))
        _record(checks, problems, "wallet_unlocks_both_scope", has_access(uid, "both"))
        _record(checks, problems, "paid_wallet_suppresses_sales", should_skip_sales(uid))
        _record(checks, problems, "wallet_user_is_auto_audio_eligible", uid in eligible_user_ids("morning"))

        canonical_demo_uid, demo_replies = handle_incoming_text(
            demo_external_uid,
            platform="max",
            external_user_id=str(demo_external_uid),
            text="demo_work",
            username="deep_demo",
            display_name="Deep Demo",
            first_name="Deep",
        )
        demo_reply = demo_replies[0]
        demo_session_id = int((demo_reply.meta or {}).get("session_id") or 0)
        demo_session = get_session(demo_session_id)
        full_before_demo = get_progress_snapshot(canonical_demo_uid)

        original_max_converter = mood_text_flow_core.ensure_max_opus_file
        mood_text_flow_core.ensure_max_opus_file = lambda path: Path(path)
        try:
            demo_result = asyncio.run(
                complete_pre_score_and_send(
                    canonical_demo_uid,
                    platform="max",
                    score=0,
                    senders=SenderRegistry(max=_SyntheticMaxSender()),
                    telegram_bot=None,
                    session_id=demo_session_id,
                )
            )
        finally:
            mood_text_flow_core.ensure_max_opus_file = original_max_converter

        full_after_demo_send = get_progress_snapshot(canonical_demo_uid)
        demo_confirmed = confirm_pending_audio_delivery(canonical_demo_uid, platform="max", sequence_key="demo")
        full_after_demo_done = get_progress_snapshot(canonical_demo_uid)
        demo_post = asyncio.run(
            complete_post_score_and_send_next(
                canonical_demo_uid,
                platform="max",
                score=2,
                senders=SenderRegistry(max=_SyntheticMaxSender()),
                telegram_bot=None,
                session_id=demo_session_id,
            )
        )
        _record(
            checks,
            problems,
            "max_demo_routes_to_demo_session",
            demo_session is not None and str(demo_session.source or "") == "demo" and demo_session.anchor_id is None,
        )
        _record(checks, problems, "demo_delivery_is_free", get_wallet(canonical_demo_uid).used_tokens == 0)
        _record(checks, problems, "demo_send_succeeds", demo_result.ok and demo_confirmed is not None and demo_post.ok)
        _record(checks, problems, "demo_usage_is_recorded", "work" in demo_sent_kinds(canonical_demo_uid))
        _record(
            checks,
            problems,
            "demo_does_not_advance_paid_full_series",
            full_before_demo.last_anchor == full_after_demo_send.last_anchor == full_after_demo_done.last_anchor
            and full_before_demo.pending_item is None
            and full_after_demo_send.pending_item is None
            and full_after_demo_done.pending_item is None,
        )

        with db() as conn:
            demo_sessions_before_repeat = _count(
                conn,
                "SELECT COUNT(*) FROM mood_sessions WHERE user_id=? AND kind='work' AND source='demo'",
                (int(canonical_demo_uid),),
            )
        _, repeat_replies = handle_incoming_text(
            demo_external_uid,
            platform="max",
            external_user_id=str(demo_external_uid),
            text="demo_work",
            username="deep_demo",
            display_name="Deep Demo",
            first_name="Deep",
        )
        with db() as conn:
            demo_sessions_after_repeat = _count(
                conn,
                "SELECT COUNT(*) FROM mood_sessions WHERE user_id=? AND kind='work' AND source='demo'",
                (int(canonical_demo_uid),),
            )
        _record(
            checks,
            problems,
            "completed_demo_cannot_be_minted_again",
            demo_sessions_before_repeat == demo_sessions_after_repeat
            and "уже" in str(repeat_replies[0].text or "").casefold(),
        )

        detail.update(
            {
                "paid_wallet": asdict(get_wallet(uid)),
                "concurrency_wallet": asdict(get_wallet(concurrency_uid)),
                "race_wallet": asdict(get_wallet(race_uid)),
                "demo_user_id": int(canonical_demo_uid),
                "demo_transport": demo_result.transport,
                "demo_message": demo_result.message,
                "check_count": len(checks),
            }
        )
        return DeepJourneyProbeResult(not problems, uid, checks, problems, detail)
    except sqlite3.Error as exc:
        problems.append(f"sqlite:{type(exc).__name__}:{exc}")
    except RuntimeError as exc:
        problems.append(f"runtime:{type(exc).__name__}:{exc}")
    except ValueError as exc:
        problems.append(f"value:{type(exc).__name__}:{exc}")
    except TypeError as exc:
        problems.append(f"type:{type(exc).__name__}:{exc}")
    except OSError as exc:
        problems.append(f"os:{type(exc).__name__}:{exc}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return DeepJourneyProbeResult(False, int(user_id), checks, problems, detail)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deep hermetic user journey scenarios")
    parser.add_argument("--user-id", type=int, default=DEFAULT_SYNTHETIC_USER_ID)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_probe(user_id=int(args.user_id))
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif result.ok:
        print(f"DEEP_USER_JOURNEY_OK checks={len(result.checks)} user_id={result.user_id}")
    else:
        print("DEEP_USER_JOURNEY_FAILED")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
