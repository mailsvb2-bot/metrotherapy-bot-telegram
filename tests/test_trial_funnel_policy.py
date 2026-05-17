from __future__ import annotations

from services.trial_funnel_policy import decide_trial_funnel_action, should_send_sales_followup


def test_trial_funnel_missing_outcome_asks_for_post_score():
    decision = decide_trial_funnel_action(None, step='offer')
    assert decision.action == 'ask_post_score'
    assert decision.allow_paid_cta is False
    assert should_send_sales_followup(None, step='offer') is False


def test_trial_funnel_low_delta_pauses_followup():
    outcome = {'quality': 'negative', 'delta': -2}
    decision = decide_trial_funnel_action(outcome, step='offer')
    assert decision.action == 'safety_pause'
    assert decision.allow_paid_cta is False
    assert should_send_sales_followup(outcome, step='offer') is False


def test_trial_funnel_zero_delta_blocks_pressure_step():
    outcome = {'quality': 'neutral', 'delta': 0}
    decision = decide_trial_funnel_action(outcome, step='offer')
    assert decision.action == 'suggest_second_demo_soft'
    assert decision.allow_pressure is False
    assert should_send_sales_followup(outcome, step='deadline') is False


def test_trial_funnel_positive_delta_allows_regular_offer():
    outcome = {'quality': 'positive', 'delta': 4}
    decision = decide_trial_funnel_action(outcome, step='offer')
    assert decision.action == 'continue_offer'
    assert decision.allow_paid_cta is True
    assert should_send_sales_followup(outcome, step='offer') is True


def test_non_sales_steps_are_not_blocked_by_trial_policy():
    assert should_send_sales_followup(None, step='remind_continue') is True
