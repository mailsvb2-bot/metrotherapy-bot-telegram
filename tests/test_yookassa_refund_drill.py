from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRILL = ROOT / "scripts" / "yookassa_refund_drill.py"
WORKER = ROOT / "scripts" / "run_deploy_worker_observed.sh"


def _load_drill():
    spec = importlib.util.spec_from_file_location("yookassa_refund_drill_contract", DRILL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_non_test_shop_is_rejected_before_waiting_or_refunding(monkeypatch) -> None:
    module = _load_drill()
    waited = False

    monkeypatch.setattr(module, "_payment_payload", lambda **_kwargs: {})
    monkeypatch.setattr(
        module,
        "_provider_request",
        lambda *_args, **_kwargs: {"id": "provider-payment", "test": False},
    )

    def fail_wait(_payment_id: str):
        nonlocal waited
        waited = True
        raise AssertionError("non-test payment must stop before provider polling")

    monkeypatch.setattr(module, "_wait_provider_payment", fail_wait)

    with pytest.raises(module.RefundDrillError, match="test_shop_required"):
        module._create_test_payment(user_id=-9138001, package_id="practice_start_7")
    assert waited is False


def test_missing_test_flag_is_rejected_fail_closed(monkeypatch) -> None:
    module = _load_drill()
    monkeypatch.setattr(module, "_payment_payload", lambda **_kwargs: {})
    monkeypatch.setattr(
        module,
        "_provider_request",
        lambda *_args, **_kwargs: {"id": "provider-payment"},
    )

    with pytest.raises(module.RefundDrillError, match="test_shop_required"):
        module._create_test_payment(user_id=-9138002, package_id="practice_start_7")


def test_test_shop_payment_advances_to_provider_poll(monkeypatch) -> None:
    module = _load_drill()
    expected = {"id": "provider-payment", "test": True, "status": "succeeded"}
    monkeypatch.setattr(module, "_payment_payload", lambda **_kwargs: {})
    monkeypatch.setattr(module, "_provider_request", lambda *_args, **_kwargs: dict(expected))
    monkeypatch.setattr(module, "_wait_provider_payment", lambda payment_id: {**expected, "id": payment_id})

    result = module._create_test_payment(user_id=-9138003, package_id="practice_start_7")

    assert result["test"] is True
    assert result["id"] == "provider-payment"


def test_provider_payment_must_remain_test_after_get(monkeypatch) -> None:
    module = _load_drill()
    monkeypatch.setattr(
        "services.payments.yookassa_provider.fetch_yookassa_payment",
        lambda _payment_id: {
            "id": "provider-payment",
            "test": False,
            "status": "succeeded",
            "paid": True,
            "refundable": True,
        },
    )

    with pytest.raises(module.RefundDrillError, match="provider_payment_not_test"):
        module._wait_provider_payment("provider-payment")


def test_redacted_provider_ids_never_publish_raw_identifier() -> None:
    module = _load_drill()
    raw = "2f1d4bd0-000f-5000-9000-secret-provider-id"

    redacted = module._redacted_id(raw)

    assert raw not in redacted
    assert redacted.startswith("sha256:")
    assert len(redacted) < len(raw) + 20


def test_documented_test_card_is_assembled_not_stored_as_contiguous_pan() -> None:
    source = DRILL.read_text(encoding="utf-8")
    module = _load_drill()

    assert "5555555555554444" not in source
    assert module._TEST_CARD_NUMBER == "5555555555554444"
    assert module._FULL_USER_ID < 0
    assert module._PARTIAL_USER_ID < 0
    assert module._RESERVED_USER_ID < 0


def test_drill_result_requires_all_three_provider_backed_scenarios(monkeypatch) -> None:
    module = _load_drill()
    module.TRIGGER_SHA = "a" * 40
    monkeypatch.setattr(module, "_require_trigger", lambda: None)
    monkeypatch.setattr(module, "_prepare_environment", lambda: None)
    monkeypatch.setattr(module, "_run_full_scenario", lambda: "full=ok")
    monkeypatch.setattr(module, "_run_partial_scenario", lambda: "partial=ok")
    monkeypatch.setattr(module, "_run_reserved_scenario", lambda: "reserved=ok")

    result = module.run_drill()

    assert "status=ok" in result
    assert "provider_test=true" in result
    assert "provider_get_refund=ok" in result
    assert "webhook_refund_succeeded=observed" in result
    assert "full=ok" in result
    assert "partial=ok" in result
    assert "reserved=ok" in result


def test_worker_runs_refund_drill_only_after_inner_deploy_success() -> None:
    source = WORKER.read_text(encoding="utf-8")

    inner = source.index('/usr/bin/bash "$INNER_WORKER"')
    inner_success_guard = source.index('if [ "$INNER_CODE" -ne 0 ]')
    marker = source.index('[yookassa-refund-live-proof-request]')
    invocation = source.index(
        'run_post_deploy_audit "yookassa_refund" "$YOOKASSA_REFUND_DRILL" 42'
    )
    completion = source.index("deploy worker completed trigger=%s")

    assert inner < inner_success_guard < marker < invocation < completion
    assert 'YOOKASSA_REFUND_DRILL="${YOOKASSA_REFUND_DRILL:-$APP_DIR/scripts/yookassa_refund_drill.py}"' in source
    assert '/usr/bin/python3 "$runner"' in source
    assert '[ops-live-proof-result]' in source


def test_drill_source_exercises_public_webhook_and_provider_refund_get() -> None:
    source = DRILL.read_text(encoding="utf-8")

    assert "/pay/yookassa/webhook" in source
    assert "fetch_yookassa_refund" in source
    assert '"refund.succeeded"' in source
    assert "refund_partial_recorded" in source
    assert "refund_action_required" in source
    assert "pending_premium_not_revoked" in source
