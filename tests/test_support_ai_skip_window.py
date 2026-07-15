from __future__ import annotations

from services.support_ai import _skip_count_last_days


def test_skip_count_uses_five_calendar_days_and_returns_real_count():
    rows = [
        {"day": "2026-07-01", "pre": 1, "post": None},
        {"day": "2026-07-05", "pre": 2, "post": None},
        {"day": "2026-07-06", "pre": 2, "post": 4},
        {"day": "2026-07-08", "pre": 3, "post": None},
        {"day": "2026-07-09", "pre": 4, "post": None},
    ]

    # The anchor is July 9, so July 5..9 is the five-day window. The July 1
    # unfinished session is correctly excluded and three recent skips remain.
    assert _skip_count_last_days(rows, days=5) == 3


def test_skip_count_falls_back_safely_for_malformed_legacy_days():
    rows = [
        {"day": "unknown", "pre": 1, "post": None},
        {"day": "", "pre": 2, "post": 3},
        {"day": None, "pre": 3, "post": None},
    ]

    assert _skip_count_last_days(rows, days=5) == 2
