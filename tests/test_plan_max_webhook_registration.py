from __future__ import annotations

from scripts.plan_max_webhook_registration import build_plan


def test_max_webhook_registration_plan_accepts_https_url():
    plan = build_plan(public_base_url="https://bot.example.test/", token="secret-token", apply=False)

    assert plan.ok is True
    assert plan.apply is False
    assert plan.webhook_url == "https://bot.example.test/webhooks/max"
    assert plan.token_configured is True
    assert "dry_run_only: pass --apply to perform registration in a future implementation" in plan.warnings
    assert plan.errors == ()


def test_max_webhook_registration_plan_rejects_unsafe_url_and_missing_token():
    plan = build_plan(public_base_url="http://bot.example.test", token="", apply=False)

    assert plan.ok is False
    assert "public_base_url must use https" in plan.errors
    assert "MAX_BOT_TOKEN is not configured" in plan.errors


def test_max_webhook_registration_plan_does_not_expose_token():
    plan = build_plan(public_base_url="https://bot.example.test", token="super-secret", apply=True)

    assert plan.ok is True
    assert plan.token_configured is True
    assert "super-secret" not in repr(plan)
