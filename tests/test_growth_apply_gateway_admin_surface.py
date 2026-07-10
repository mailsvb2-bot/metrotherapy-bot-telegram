from __future__ import annotations

from handlers.admin_reports import growth_autopilot


def _callbacks(markup) -> list[str]:
    out: list[str] = []
    for row in markup.inline_keyboard:
        for button in row:
            data = getattr(button, "callback_data", None)
            if data:
                out.append(str(data))
    return out


def test_guarded_apply_admin_surface_is_read_only():
    callbacks = _callbacks(growth_autopilot._apply_kb("today"))

    assert "admin:growth:autopilot:conversions:today" in callbacks
    assert "admin:growth:autopilot:report:today" in callbacks
    assert not any("approve" in callback for callback in callbacks)
    assert not any("reject" in callback for callback in callbacks)
    assert not any("dispatch" in callback for callback in callbacks)
    assert not any("execute" in callback for callback in callbacks)
