from __future__ import annotations

from handlers.mood_flow.ratings import _trial_outcome_keyboard


def _flatten_buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_trial_outcome_graph_button_points_to_current_post_chart():
    markup = _trial_outcome_keyboard(123, "work", delta=2, session_id=456)
    buttons = _flatten_buttons(markup)
    graph_buttons = [button for button in buttons if "график" in button.text.lower()]

    assert graph_buttons
    assert graph_buttons[0].callback_data == "post:chart:456"
