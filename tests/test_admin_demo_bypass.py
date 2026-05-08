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
