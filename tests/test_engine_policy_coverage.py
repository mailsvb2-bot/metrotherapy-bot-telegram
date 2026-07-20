from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from core import engine as engine_module


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


class Bot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, Any]] = []

    async def send_message(self, user_id: int, text: str, reply_markup: Any = None) -> None:
        self.messages.append((user_id, text, reply_markup))


def test_engine_sync_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert engine_module._parse_dt("2026-07-20T12:00:00+00:00").year == 2026
    assert engine_module._funnel_kind("work") == "work"
    assert engine_module._funnel_kind("home") == "home"
    assert engine_module._funnel_kind("both") == "both"
    assert engine_module._funnel_kind("bad", default="home") == "home"

    monkeypatch.setattr(engine_module, "is_sub_active", lambda uid: uid == 1)
    assert engine_module._is_sub_active_sync(1) is True
    monkeypatch.setattr(engine_module, "demo_sent_kinds", lambda uid: ["work", str(uid)])
    monkeypatch.setattr(engine_module, "can_repeat_demo_for_user", lambda uid: uid == 7)
    assert engine_module._demo_send_state_sync(7) == ({"work", "7"}, True)
    monkeypatch.setattr(engine_module, "create_session", lambda *args, **kwargs: 15)
    assert engine_module._create_demo_session_sync(2, kind="work", day="2026-07-20") == 15
    assert engine_module._create_demo_session_sync(2, kind="other", day="2026-07-20") == 15

    class Conn:
        def __init__(self, row: Any) -> None:
            self.row = row

        def execute(self, *_args: Any) -> Any:
            return SimpleNamespace(fetchone=lambda: self.row)

    monkeypatch.setattr(engine_module, "get_db", lambda: DbContext(Conn({"ack_at_utc": "now"})))
    assert engine_module._demo_ack_exists_sync(user_id=1, kind="work", message_id=2) is True
    monkeypatch.setattr(engine_module, "first_hour_today_local", lambda *_args: None)
    monkeypatch.setattr(engine_module, "recent_hour_local", lambda *_args: 17)
    assert engine_module._hour_context_sync(1) == 17
    monkeypatch.setattr(engine_module, "has_event_since", lambda *args: True)
    assert engine_module._has_viewed_tariffs_since_sync(user_id=1, ack_at="x") is True

    monkeypatch.setattr(engine_module, "is_sub_active", lambda uid: False)
    assert engine_module._paid_setup_already_configured_sync(1) is False
    monkeypatch.setattr(engine_module, "is_sub_active", lambda uid: True)
    monkeypatch.setattr(engine_module, "get_db", lambda: DbContext(Conn({"work_time": "08:00", "home_time": None})))
    assert engine_module._paid_setup_already_configured_sync(1) is True

    import services.mood

    monkeypatch.setattr(services.mood, "get_session", lambda sid: SimpleNamespace(kind="home"))
    assert engine_module._post_prompt_idem_kind_sync("3") == "home"
    monkeypatch.setattr(services.mood, "get_session", lambda sid: None)
    assert engine_module._post_prompt_idem_kind_sync("3") == ""
    assert engine_module._post_prompt_idem_kind_sync("bad") == ""


def test_funnel_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "already_sent", lambda *_args: True)
    assert engine_module._funnel2_demo_nopay_guard_sync(1, {}) == "already_sent"
    monkeypatch.setattr(engine_module, "already_sent", lambda *_args: False)
    monkeypatch.setattr(engine_module, "eligible_demo_nopay_24h", lambda uid: False)
    monkeypatch.setattr(engine_module, "log_skip", lambda *args: None)
    assert engine_module._funnel2_demo_nopay_guard_sync(1, {}) == "not_eligible"
    monkeypatch.setattr(engine_module, "eligible_demo_nopay_24h", lambda uid: True)
    monkeypatch.setattr(engine_module, "mark_sent", lambda *args: False)
    assert engine_module._funnel2_demo_nopay_guard_sync(1, {}) == "duplicate"
    monkeypatch.setattr(engine_module, "mark_sent", lambda *args: True)
    assert engine_module._funnel2_demo_nopay_guard_sync(1, {"kind": "work"}) == "send"

    monkeypatch.setattr(engine_module, "eligible_expired_return_3d", lambda uid: False)
    assert engine_module._funnel2_expired_return_guard_sync(1, {}) == "not_eligible"
    monkeypatch.setattr(engine_module, "eligible_expired_return_3d", lambda uid: True)
    monkeypatch.setattr(engine_module, "mark_sent", lambda *args: False)
    assert engine_module._funnel2_expired_return_guard_sync(1, {}) == "duplicate"
    monkeypatch.setattr(engine_module, "mark_sent", lambda *args: True)
    assert engine_module._funnel2_expired_return_guard_sync(1, {}) == "send"


def test_engine_keyboards_and_registry() -> None:
    engine = engine_module.Engine()
    kb = engine._kb_after_demo("work", message_id=12)
    assert kb.inline_keyboard[0][0].callback_data == "demo:ack:work:12"
    assert engine._kb_after_demo("home", allow_other=False).inline_keyboard[-1][0].callback_data == "menu:main"
    assert engine._kb_funnel(1).inline_keyboard[0][0].callback_data == "sub:menu"
    assert engine._kb_offer(1).inline_keyboard[1][0].callback_data == "gift:menu"
    assert {"demo_send", "post_prompt", "sub_expiring_soon"}.issubset(engine._job_handlers())


@pytest.mark.asyncio
async def test_execute_job_unknown_denied_and_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = engine_module.Engine()
    events: list[Any] = []
    tokens: list[Any] = []
    monkeypatch.setattr(engine_module, "log_event", lambda *args: events.append(args))
    await engine._execute_job(Bot(), engine_module.Job(1, 2, "unknown", "now", "{}"), {})
    assert events[-1][1] == "job_unknown"

    decision = SimpleNamespace(token="token", decision_id="decision", payload={"type": "denied", "reason": "policy"})
    monkeypatch.setattr(engine_module, "DecisionCore", SimpleNamespace(instance=lambda: SimpleNamespace(decide=lambda state: decision)))
    monkeypatch.setattr(engine_module, "require_token", lambda token: tokens.append(("require", token)))
    monkeypatch.setattr(engine_module, "set_current_token", lambda token: tokens.append(("set", token)))
    await engine._execute_job(Bot(), engine_module.Job(2, 3, "demo_reminder", "now", "{}"), {"b": 1, "a": 2})
    assert events[-1][1] == "job_policy_denied"

    called: list[Any] = []

    async def handler(bot: Any, uid: int, payload: dict) -> None:
        called.append((uid, payload))

    monkeypatch.setattr(engine, "_job_handlers", lambda: {"custom": handler})
    decision.payload = {"type": "job_execution_allowed"}
    await engine._execute_job(Bot(), engine_module.Job(3, 4, "custom", "now", "{}"), {"x": 1})
    assert called == [(4, {"x": 1})]
    assert tokens[-2:] == [("set", "token"), ("set", None)]
