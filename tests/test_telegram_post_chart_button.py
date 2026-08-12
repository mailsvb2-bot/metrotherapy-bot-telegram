from __future__ import annotations

from handlers.mood_flow.ratings import _trial_outcome_keyboard


def _flatten_buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_trial_outcome_graph_button_points_to_current_post_chart():
    markup = _trial_outcome_keyboard(
        123,
        "work",
        allow_paid_cta=True,
        session_id=456,
    )
    buttons = _flatten_buttons(markup)
    graph_buttons = [button for button in buttons if "график" in button.text.lower()]

    assert graph_buttons
    assert graph_buttons[0].callback_data == "post:chart:456"


def test_trial_outcome_keyboard_hides_paid_cta_when_policy_blocks_it():
    markup = _trial_outcome_keyboard(
        123,
        "work",
        allow_paid_cta=False,
        session_id=456,
    )
    buttons = _flatten_buttons(markup)

    assert not any(button.callback_data == "sub:menu" for button in buttons)
    assert any(button.callback_data == "post:chart:456" for button in buttons)
