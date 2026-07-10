from __future__ import annotations

from handlers.admin_inline_common import AdminCtx
from handlers.admin_reports import growth_autopilot
from services.admin_permissions import GROWTH_APPLY_REVIEW_PERMISSION, PERMS


def _ctx(*, superadmin: bool, allowed_perms):
    return AdminCtx(
        uid=100,
        roles={"admin"},
        staff_kb=None,
        is_superadmin=superadmin,
        allowed_perms=allowed_perms,
    )


def _callbacks(markup) -> list[str]:
    return [
        str(button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_review_controls_require_superadmin_or_explicit_permission():
    assert growth_autopilot._can_render_review_controls(
        _ctx(superadmin=True, allowed_perms=None)
    ) is True
    assert growth_autopilot._can_render_review_controls(
        _ctx(superadmin=False, allowed_perms=None)
    ) is False
    assert growth_autopilot._can_render_review_controls(
        _ctx(superadmin=False, allowed_perms={"admin:growth:autopilot"})
    ) is False
    assert growth_autopilot._can_render_review_controls(
        _ctx(superadmin=False, allowed_perms={GROWTH_APPLY_REVIEW_PERMISSION})
    ) is True


def test_review_permission_is_exposed_in_admin_permission_catalog():
    assert any(item.perm == GROWTH_APPLY_REVIEW_PERMISSION for item in PERMS)


def test_read_only_request_card_has_no_review_callbacks():
    preview = {
        "request": {"id": 42},
        "can_approve": True,
        "can_reject": True,
    }
    callbacks = _callbacks(
        growth_autopilot._request_kb("today", preview, can_review=False)
    )

    assert not any(":prep:" in callback for callback in callbacks)
    assert not any("execute" in callback or "dispatch" in callback for callback in callbacks)


def test_review_card_uses_prepare_step_before_final_confirmation():
    preview = {
        "request": {"id": 42},
        "can_approve": True,
        "can_reject": True,
    }
    callbacks = _callbacks(
        growth_autopilot._request_kb("month", preview, can_review=True)
    )

    assert "admin:growth:autopilot:apply:prep:approve:42:month" in callbacks
    assert "admin:growth:autopilot:apply:prep:reject:42:month" in callbacks
    assert not any(":confirm:" in callback for callback in callbacks)


def test_confirmation_callbacks_are_short_and_contain_no_execute_path():
    markup = growth_autopilot._confirmation_kb(
        "month",
        {"token": "AbCdEf12345", "decision": "approve"},
    )
    callbacks = _callbacks(markup)

    assert "admin:growth:autopilot:apply:confirm:AbCdEf12345:month" in callbacks
    assert "admin:growth:autopilot:apply:cancel:AbCdEf12345:month" in callbacks
    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)
    assert not any("execute" in callback or "dispatch" in callback for callback in callbacks)


def test_callback_parsing_rejects_direct_decision_shortcuts():
    assert growth_autopilot._request_id_from_callback(
        "admin:growth:autopilot:apply:prep:approve:77:today"
    ) == 77
    assert growth_autopilot._decision_from_callback(
        "admin:growth:autopilot:apply:prep:reject:77:today"
    ) == "reject"
