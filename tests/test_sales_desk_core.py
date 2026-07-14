from __future__ import annotations

import pytest

from services.sales_desk_core import (
    assert_transition,
    can_transition,
    compact_display_name,
    extract_attribution,
    lead_key,
    normalize_filter,
    sanitize_note,
    should_auto_advance,
    stage_from_event_names,
)


def test_sales_stage_progression_and_reopen_contract() -> None:
    assert can_transition("new", "contacted") is True
    assert can_transition("contacted", "qualified") is True
    assert can_transition("qualified", "checkout") is True
    assert can_transition("checkout", "won") is True
    assert can_transition("lost", "new") is True
    assert can_transition("new", "won") is False

    with pytest.raises(ValueError, match="sales_stage_transition_not_allowed"):
        assert_transition("new", "won")


def test_event_signals_choose_highest_sales_stage() -> None:
    assert stage_from_event_names(["funnel_start_command"]) == "new"
    assert stage_from_event_names(["demo_ack", "sub_menu_open"]) == "qualified"
    assert stage_from_event_names(["payment_started", "payment_success"]) == "won"


def test_manual_stage_is_not_silently_overwritten_except_verified_win() -> None:
    assert should_auto_advance("contacted", "qualified", stage_source="manual") is False
    assert should_auto_advance("lost", "qualified", stage_source="auto") is False
    assert should_auto_advance("lost", "won", stage_source="manual") is True


def test_attribution_and_identity_are_bounded_and_deterministic() -> None:
    result = extract_attribution(
        '{"utm_source":"tgads","utm_campaign":"summer"}',
        {"attribution": {"creative": "video-01"}},
    )
    assert result == {
        "source": "tgads",
        "campaign": "summer",
        "creative": "video-01",
    }
    assert lead_key(123) == "user:123"
    assert compact_display_name(first_name="Анна", username="anna", user_id=123) == "Анна (@anna)"


def test_note_and_filter_normalization() -> None:
    assert sanitize_note("  созвон   завтра  ") == "созвон завтра"
    assert normalize_filter("OVERDUE") == "overdue"
    assert normalize_filter("unknown") == "open"
    with pytest.raises(ValueError, match="sales_note_empty"):
        sanitize_note("  ")
