from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services import probe_safety

ROOT = Path(__file__).resolve().parents[1]


def test_probe_mutation_authorization_is_explicit_or_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(probe_safety.PROBE_MUTATION_AUTH_ENV, raising=False)
    assert probe_safety.mutation_authorized(False) is False
    assert probe_safety.mutation_authorized(True) is True

    monkeypatch.setenv(probe_safety.PROBE_MUTATION_AUTH_ENV, "0")
    assert probe_safety.mutation_authorized(False) is False

    monkeypatch.setenv(probe_safety.PROBE_MUTATION_AUTH_ENV, "1")
    assert probe_safety.mutation_authorized(False) is True


def test_synthetic_user_ids_are_unique_and_reserved(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter(
        [
            SimpleNamespace(hex="000000000011" + "0" * 20),
            SimpleNamespace(hex="000000000012" + "0" * 20),
        ]
    )
    monkeypatch.setattr(probe_safety.uuid, "uuid4", lambda: next(values))

    first = probe_safety.new_synthetic_user_id()
    second = probe_safety.new_synthetic_user_id()

    assert first != second
    assert probe_safety.SYNTHETIC_USER_ID_MIN <= first <= probe_safety.SYNTHETIC_USER_ID_MAX
    assert probe_safety.SYNTHETIC_USER_ID_MIN <= second <= probe_safety.SYNTHETIC_USER_ID_MAX


def test_safe_probe_error_code_never_contains_exception_text() -> None:
    secret = "postgresql://user:password@example.invalid/db"
    code = probe_safety.safe_probe_error_code(RuntimeError(secret))

    assert code == "probe_failure:RuntimeError"
    assert secret not in code


def test_production_gate_scopes_mutation_authorization_after_restore_preflight() -> None:
    source = (ROOT / "scripts" / "production_gate.py").read_text(encoding="utf-8")

    restore_check = source.index("if not _restore_target_configured(gate_env):")
    authorization = source.index('gate_env[PROBE_MUTATION_AUTH_ENV] = "1"')
    post_deploy = source.index('"scripts/post_deploy_verify.py"')

    assert restore_check < authorization < post_deploy
    assert 'env=gate_env' in source[post_deploy : post_deploy + 500]
    assert "os.environ[PROBE_MUTATION_AUTH_ENV]" not in source
