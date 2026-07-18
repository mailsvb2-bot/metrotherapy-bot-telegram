from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import probe_payment_reconciliation_live as probe_module
from services.probe_ledger import SYNTHETIC_USER_ID_MAX, SYNTHETIC_USER_ID_MIN


def _bomb(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("database mutation must not be reached")


def test_dry_run_never_initializes_schema_or_writes_probe_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_module, "init_db", _bomb)
    monkeypatch.setattr(probe_module, "start_probe_run", _bomb)
    monkeypatch.setattr(probe_module, "finish_probe_run", _bomb)
    monkeypatch.setattr(probe_module, "_snapshot", _bomb)
    monkeypatch.setattr(probe_module, "_cleanup_probe_rows", _bomb)
    monkeypatch.setattr(probe_module, "record_yookassa_webhook", _bomb)

    result = probe_module.probe(
        package_id=probe_module.DEFAULT_PACKAGE_ID,
        user_id=-910_000_301,
        source="telegram",
        apply=False,
        cleanup=True,
    )

    assert result.applied is False
    assert result.first_ok is True
    assert result.first_inserted is False
    assert result.first_problem == "dry_run"
    assert result.cleanup_status == "dry_run"
    assert result.rows_touched == 0
    assert result.payment_id.startswith(f"{probe_module.PAYMENT_ID_PREFIX}-")


@pytest.mark.parametrize(
    "argv",
    [
        ["probe", "--apply-webhooks"],
        ["probe", "--allow-live-db-mutation"],
        ["probe", "--keep-artifacts"],
    ],
)
def test_incomplete_mutation_authorization_fails_before_probe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(probe_module, "probe", _bomb)

    assert probe_module.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["applied"] is False
    assert payload["database_touched"] is False
    assert payload["error_code"] in {
        "mutation_flags_must_be_used_together",
        "keep_artifacts_requires_authorized_mutation",
    }


def test_authorized_mutation_requires_both_flags() -> None:
    assert probe_module._resolve_apply_mode(
        apply_webhooks=True,
        allow_live_db_mutation=True,
        keep_artifacts=False,
    ) == (True, None)
    assert probe_module._resolve_apply_mode(
        apply_webhooks=True,
        allow_live_db_mutation=True,
        keep_artifacts=True,
    ) == (True, None)


def test_generated_user_ids_are_unique_and_reserved(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter(
        [
            SimpleNamespace(hex="000000000001" + "0" * 20),
            SimpleNamespace(hex="000000000002" + "0" * 20),
        ]
    )
    monkeypatch.setattr(probe_module.uuid, "uuid4", lambda: next(values))

    first = probe_module._new_synthetic_user_id()
    second = probe_module._new_synthetic_user_id()

    assert first != second
    assert SYNTHETIC_USER_ID_MIN <= first <= SYNTHETIC_USER_ID_MAX
    assert SYNTHETIC_USER_ID_MIN <= second <= SYNTHETIC_USER_ID_MAX


def test_cleanup_targets_exact_payment_prefix_and_canonical_account_rows(
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

    monkeypatch.setattr(probe_module, "db", lambda: Context())
    payment_id = "synthetic-probe-yookassa-practice_personal_month-test"

    touched = probe_module._cleanup_probe_rows(user_id=-910_000_301, payment_id=payment_id)

    sql_text = "\n".join(sql for sql, _params in executed)
    assert touched == len(executed)
    assert "DELETE FROM account_channel_identities" in sql_text
    assert "DELETE FROM accounts" in sql_text
    assert "DELETE FROM account_audio_progress" in sql_text
    assert " LIKE " not in sql_text
    outbox = [
        (sql, params)
        for sql, params in executed
        if "DELETE FROM premium_delivery_outbox WHERE substr(idempotency_key" in sql
    ]
    prefix = f"premium_delivery:yookassa:{payment_id}:"
    assert outbox == [
        (
            "DELETE FROM premium_delivery_outbox WHERE substr(idempotency_key, 1, ?)=?",
            (len(prefix), prefix),
        )
    ]


def test_applied_probe_records_clean_only_after_zero_residual(monkeypatch: pytest.MonkeyPatch) -> None:
    package = SimpleNamespace(
        package_id=probe_module.DEFAULT_PACKAGE_ID,
        price_rub=1,
        tokens=60,
    )
    zero = {
        "wallet": 0,
        "users": 0,
        "payments": 0,
        "payment_grants": 0,
        "entitlements": 0,
        "outbox": 0,
        "consultation": 0,
        "accounts": 0,
        "identities": 0,
    }
    after = {
        "wallet": 60,
        "users": 1,
        "payments": 1,
        "payment_grants": 1,
        "entitlements": 2,
        "outbox": 2,
        "consultation": 1,
        "accounts": 1,
        "identities": 0,
    }
    snapshots = iter([zero, after, zero])
    reconciliation = iter(
        [
            SimpleNamespace(ok=True, inserted=True, problem=""),
            SimpleNamespace(ok=True, inserted=False, problem=""),
        ]
    )
    finished: dict[str, Any] = {}

    monkeypatch.setattr(probe_module, "package_by_id", lambda _package_id: package)
    monkeypatch.setattr(probe_module, "init_db", lambda: None)
    monkeypatch.setattr(probe_module, "start_probe_run", lambda **_kwargs: None)
    monkeypatch.setattr(probe_module, "finish_probe_run", lambda **kwargs: finished.update(kwargs))
    monkeypatch.setattr(probe_module, "_cleanup_probe_rows", lambda **_kwargs: 3)
    monkeypatch.setattr(probe_module, "_snapshot", lambda **_kwargs: next(snapshots))
    monkeypatch.setattr(probe_module, "record_yookassa_webhook", lambda _payload: next(reconciliation))

    result = probe_module.probe(
        package_id=probe_module.DEFAULT_PACKAGE_ID,
        user_id=-910_000_301,
        source="telegram",
        apply=True,
        cleanup=True,
    )

    assert result.cleanup_status == "clean"
    assert result.residual_rows == 0
    assert result.wallet_delta == 60
    assert finished["status"] == "ok"
    assert finished["cleanup_status"] == "clean"
