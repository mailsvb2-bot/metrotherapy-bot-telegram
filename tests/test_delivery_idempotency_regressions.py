from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from services.auto_audio import _is_due_local_day, _matches_slot_second
from services.idempotency_keys import for_demo_click, for_session


def _legacy_demo_key_from_rating_handler_shape(session_id: int) -> int:
    # Mirrors the legacy handlers.mood_flow.ratings shape where `sid` is a local
    # variable and for_demo_click() is called with no args.
    sid = int(session_id)
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
