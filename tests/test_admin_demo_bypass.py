from __future__ import annotations

import core.engine as engine
import handlers.demo as demo
import services.demo_policy as demo_policy


def test_admin_demo_policy_bypasses_repeat_limit(monkeypatch):
    monkeypatch.setattr(demo_policy, "is_admin", lambda user_id: True)

    assert demo_policy.can_repeat_demo_for_user(768478185) is True


def test_regular_user_demo_policy_does_not_bypass_repeat_limit(monkeypatch):
    monkeypatch.setattr(demo_policy, "is_admin", lambda user_id: False)

    assert demo_policy.can_repeat_demo_for_user(768478185) is False


def test_demo_handlers_use_canonical_admin_bypass_policy():
    assert demo.can_repeat_demo_for_user is demo_policy.can_repeat_demo_for_user


def test_engine_uses_canonical_admin_bypass_policy():
    assert engine.can_repeat_demo_for_user is demo_policy.can_repeat_demo_for_user


def test_trial_status_for_new_regular_user(monkeypatch):
    monkeypatch.setattr(demo_policy, "is_admin", lambda user_id: False)

    status = demo_policy.trial_status_for_user(1, [])

    assert status.stage == "not_started"
    assert status.sent_kinds == ()
    assert status.remaining_kinds == ("work", "home")
    assert status.exhausted is False


def test_trial_status_for_partially_used_regular_user(monkeypatch):
    monkeypatch.setattr(demo_policy, "is_admin", lambda user_id: False)

    status = demo_policy.trial_status_for_user(1, ["work", "unknown", "work"])

    assert status.stage == "started"
    assert status.sent_kinds == ("work",)
    assert status.remaining_kinds == ("home",)
    assert demo_policy.can_receive_demo_kind(1, "home", status.sent_kinds) is True
    assert demo_policy.can_receive_demo_kind(1, "work", status.sent_kinds) is False


def test_trial_status_for_exhausted_regular_user(monkeypatch):
    monkeypatch.setattr(demo_policy, "is_admin", lambda user_id: False)

    status = demo_policy.trial_status_for_user(1, ["home", "work"])

    assert status.stage == "completed_both"
    assert status.sent_kinds == ("work", "home")
    assert status.remaining_kinds == ()
    assert status.exhausted is True


def test_trial_status_for_admin_keeps_bypass(monkeypatch):
    monkeypatch.setattr(demo_policy, "is_admin", lambda user_id: True)

    status = demo_policy.trial_status_for_user(1, ["work", "home"])

    assert status.stage == "admin_bypass"
    assert status.remaining_kinds == ("work", "home")
    assert status.exhausted is False
    assert demo_policy.can_receive_demo_kind(1, "work", status.sent_kinds) is True


def test_next_remaining_demo_kind_prefers_opposite_then_any_remaining():
    assert demo_policy.next_remaining_demo_kind("work", ["work"]) == "home"
    assert demo_policy.next_remaining_demo_kind("home", ["home"]) == "work"
    assert demo_policy.next_remaining_demo_kind("work", ["work", "home"]) is None
