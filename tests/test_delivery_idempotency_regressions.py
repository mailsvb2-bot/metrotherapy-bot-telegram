from __future__ import annotations

import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

import services.auto_audio as auto_audio
import services.mood_text_flow as mood_flow
import services.mood_text_flow_core as mood_flow_core
from services.auto_audio import _is_due_local_day, _matches_slot_second
from services.idempotency_keys import for_demo_click, for_session


def _legacy_demo_key_from_rating_handler_shape(session_id: int) -> int:
    # Mirrors the legacy handler call shape where `sid` is a local variable and
    # for_demo_click() is called without explicit args.
    sid = int(session_id)  # noqa: F841 - canonical legacy caller-local contract
    return for_demo_click()


def test_legacy_demo_click_uses_session_id_not_wall_second_bucket() -> None:
    assert _legacy_demo_key_from_rating_handler_shape(101) == for_session(101)
    assert _legacy_demo_key_from_rating_handler_shape(101) == _legacy_demo_key_from_rating_handler_shape(101)
    assert _legacy_demo_key_from_rating_handler_shape(101) != _legacy_demo_key_from_rating_handler_shape(102)


def test_explicit_demo_click_can_be_user_and_session_stable() -> None:
    assert for_demo_click(1, session_id=77) == for_demo_click(1, session_id=77)
    assert for_demo_click(1, session_id=77) != for_demo_click(2, session_id=77)
    assert for_demo_click(1, session_id=77) != for_demo_click(1, session_id=78)


def test_auto_audio_exact_second_contract_is_backward_compatible() -> None:
    tz = ZoneInfo("Europe/Moscow")
    assert _matches_slot_second(datetime(2026, 6, 2, 8, 30, 1, tzinfo=tz), "08:30")
    assert not _matches_slot_second(datetime(2026, 6, 2, 8, 30, 2, tzinfo=tz), "08:30")


def test_auto_audio_due_window_survives_slow_scheduler_tick() -> None:
    tz = ZoneInfo("Europe/Moscow")
    assert not _is_due_local_day(datetime(2026, 6, 2, 8, 30, 0, tzinfo=tz), "08:30")
    assert _is_due_local_day(datetime(2026, 6, 2, 8, 30, 1, tzinfo=tz), "08:30")
    assert _is_due_local_day(datetime(2026, 6, 2, 8, 45, 0, tzinfo=tz), "08:30")
    assert not _is_due_local_day(datetime(2026, 6, 3, 0, 0, 0, tzinfo=tz), "23:59:59")


def test_mood_audio_send_path_uses_canonical_lock_and_token_effects() -> None:
    facade_source = inspect.getsource(mood_flow.complete_pre_score_and_send)
    core_source = inspect.getsource(mood_flow_core.complete_pre_score_and_send)

    assert "acquire_delivery_lock" in facade_source
    assert "audio_lock" in facade_source
    assert "_core.complete_pre_score_and_send" in facade_source
    assert "mark_delivery_once" in facade_source
    assert "_cleanup_audio_lock" in facade_source

    assert "check_and_reserve_for_audio" in core_source
    assert "finalize_audio_access" in core_source
    assert "delivered=True" in core_source
    assert "delivered=False" in core_source


def test_auto_audio_prompt_path_uses_reclaimable_pre_score_lock() -> None:
    tick_source = inspect.getsource(auto_audio.tick)
    effect_source = inspect.getsource(auto_audio._process_due_candidate)

    assert "_run_candidate_workers" in tick_source
    assert "_process_due_candidate" in inspect.getsource(auto_audio._run_candidate_workers)
    assert "acquire_delivery_lock" in effect_source
    assert "pre_score_lock" in effect_source
    assert 'final_stage="pre_score"' in effect_source
    assert "auto_audio_stale_lock_reclaimed" in effect_source
    assert "finally:" in effect_source
    assert "_unmark_pre_score_lock" in effect_source
