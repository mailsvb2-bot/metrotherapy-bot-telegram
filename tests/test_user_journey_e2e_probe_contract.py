from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "probe_user_journey_e2e.py"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_user_journey_e2e_probe_declares_canonical_probe_type() -> None:
    text = _source()
    assert 'PROBE_TYPE = "synthetic_user_journey_e2e_probe"' in text
    assert 'PAYMENT_ID_PREFIX = "synthetic-probe-user-journey"' in text
    assert "record_yookassa_webhook" in text
    assert "check_and_reserve_for_audio" in text
    assert "finalize_audio_access" in text
    assert "record_demo_ack" in text


def test_user_journey_e2e_probe_has_no_broad_exception_handlers() -> None:
    tree = ast.parse(_source())
    broad_handlers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            broad_handlers.append("bare")
            continue
        if isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}:
            broad_handlers.append(node.type.id)
    assert broad_handlers == []


def test_user_journey_e2e_probe_defaults_to_verified_cleanup() -> None:
    text = _source()
    assert "keep_artifacts: bool = False" in text
    assert "allow_live_db_mutation: bool" in text
    assert "require_live_db_mutation" in text
    assert "residual_rows: int" in text
    assert 'cleanup_status = "clean" if residual_rows == 0 else "residual"' in text
    assert 'problems.append(f"cleanup_residual_rows:{residual_rows}")' in text
    assert "DELETE FROM users WHERE user_id=?" in text
    assert "DELETE FROM accounts WHERE account_id=? OR primary_user_id=?" in text
    assert "DELETE FROM account_audio_progress WHERE account_id=?" in text


def test_user_journey_e2e_probe_uses_exact_payment_outbox_matching() -> None:
    text = _source()
    assert "idempotency_key LIKE" not in text
    assert "substr(idempotency_key, 1, ?)=?" in text
    assert "_outbox_prefix(payment_id)" in text


def test_user_journey_failure_evidence_uses_safe_error_codes() -> None:
    text = _source()
    assert "safe_probe_error_code" in text
    assert 'error_code = deps["safe_probe_error_code"](error)' in text
    assert 'f"unexpected:{type(exc).__name__}:{exc}"' not in text
