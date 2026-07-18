from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import probe_user_journey_e2e as probe_module
from scripts import user_scenario_gate
from services.probe_safety import ProbeMutationAuthorizationRequired


def test_user_journey_probe_refuses_before_schema_init(monkeypatch: pytest.MonkeyPatch) -> None:
    def require(allowed: bool) -> None:
        if not allowed:
            raise ProbeMutationAuthorizationRequired("probe_mutation_authorization_required")

    def bomb() -> None:
        raise AssertionError("schema initialization must not run")

    monkeypatch.setattr(
        probe_module,
        "_imports",
        lambda: {
            "require_live_db_mutation": require,
            "init_db": bomb,
        },
    )

    with pytest.raises(ProbeMutationAuthorizationRequired):
        probe_module.run_probe(
            user_id=-910_000_501,
            keep_artifacts=False,
            allow_live_db_mutation=False,
        )


def test_user_journey_cleanup_uses_exact_outbox_prefix_and_account_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, tuple[Any, ...]]] = []

    class Cursor:
        rowcount = 1

    class Connection:
        def execute(self, sql: str, params: tuple[Any, ...]) -> Cursor:
            executed.append((" ".join(sql.split()), params))
            return Cursor()

    class Context:
        def __enter__(self) -> Connection:
            return Connection()

        def __exit__(self, *_args: Any) -> None:
            return None

    payment_id = "synthetic-probe-user-journey-practice_personal_month-test"
    touched = probe_module._cleanup_probe_rows(
        db=lambda: Context(),
        assert_synthetic_user_id=lambda _user_id: None,
        user_id=-910_000_501,
        payment_id=payment_id,
    )

    sql_text = "\n".join(sql for sql, _params in executed)
    assert touched == len(executed)
    assert " LIKE " not in sql_text
    for table in (
        "account_audio_completions",
        "account_audio_deliveries",
        "account_audio_progress",
        "account_channel_identities",
        "accounts",
    ):
        assert f"DELETE FROM {table}" in sql_text

    outbox_rows = [
        (sql, params)
        for sql, params in executed
        if "DELETE FROM premium_delivery_outbox WHERE substr(idempotency_key" in sql
    ]
    prefix = f"premium_delivery:yookassa:{payment_id}:"
    assert outbox_rows == [
        (
            "DELETE FROM premium_delivery_outbox WHERE substr(idempotency_key, 1, ?)=?",
            (len(prefix), prefix),
        )
    ]


def _successful_probe_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "cleanup_status": "clean",
        "residual_rows": 0,
        "problems": [],
        "demo_ack_ok": True,
        "wallet_delta_after_payment": 60,
        "entitlement_rows_delta": 2,
        "outbox_rows_delta": 2,
        "consultation_rows_delta": 1,
        "used_tokens_after_paid_audio": 1,
        "rows_touched": 20,
    }


def test_hermetic_scenario_gate_explicitly_authorizes_only_disposable_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_successful_probe_payload(), ensure_ascii=False),
            stderr="",
        )

    monkeypatch.setattr(user_scenario_gate.subprocess, "run", fake_run)
    result = user_scenario_gate.run_gate(
        mode="hermetic",
        env_file="",
        user_id=-910_000_701,
        keep_artifacts=False,
        timeout_sec=30,
    )

    assert result.ok is True
    assert "--allow-live-db-mutation" in captured["command"]
    assert captured["env"]["METRO_DB_ENGINE"] == "sqlite"
    assert captured["env"]["DATABASE_URL"] == ""


def test_prod_scenario_gate_does_not_self_authorize_live_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_successful_probe_payload(), ensure_ascii=False),
            stderr="",
        )

    monkeypatch.setattr(user_scenario_gate.subprocess, "run", fake_run)
    result = user_scenario_gate.run_gate(
        mode="prod",
        env_file="/etc/metrotherapy/metrotherapy.env",
        user_id=-910_000_702,
        keep_artifacts=False,
        timeout_sec=30,
    )

    assert result.ok is True
    assert "--allow-live-db-mutation" not in captured["command"]


def test_scenario_gate_rejects_nonzero_cleanup_residual() -> None:
    payload = _successful_probe_payload()
    payload["residual_rows"] = 1

    checks = user_scenario_gate._build_checks(payload, returncode=0, keep_artifacts=False)

    assert checks["zero_residual_after_cleanup"] is False
